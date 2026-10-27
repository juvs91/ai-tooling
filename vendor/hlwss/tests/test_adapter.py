"""Tests del Adapter — fan-out PLE gemma3n (ADR-0067) y tamaños de fila.

Fixtures falsos en memoria: un modelo mínimo con los nombres de módulo de
Gemma3nForConditionalGeneration dispara los hooks reales del adapter; sin
descargar el modelo.
"""

import torch
from torch import nn

from hlwss.adapter_torch import Adapter
from hlwss.observer import Observer, RunConfig
from hlwss.registry import BlockRegistry
from tests.test_registry import make_gemma3n_cfg


class _Container(nn.Module):
    def __init__(self, *mods):
        super().__init__()
        for i, m in enumerate(mods):
            setattr(self, f"m{i}", m)

    def forward(self, x):  # el cuerpo da igual: el test solo dispara hooks
        return x


class _Layer(nn.Module):
    def __init__(self, h, inter):
        super().__init__()
        self.self_attn = _Container(nn.Linear(h, h, bias=False))
        self.mlp = _Container(nn.Linear(h, inter, bias=False),
                              nn.Linear(h, inter, bias=False),
                              nn.Linear(inter, h, bias=False))
        self.laurel = _Container(nn.Linear(h, 32, bias=False))
        self.altup = _Container(nn.Linear(h, 4, bias=False))
        self.per_layer_input_gate = nn.Linear(h, 64, bias=False)
        self.per_layer_projection = nn.Linear(64, h, bias=False)


class _TextModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.embed_tokens = nn.Embedding(1000, 256)
        self.embed_tokens_per_layer = nn.Embedding(900, 64 * 3)
        self.per_layer_model_projection = nn.Linear(256, 64 * 3, bias=False)
        self.layers = nn.ModuleList([_Layer(256, s) for s in (512, 768, 512)])


class _Inner(nn.Module):
    def __init__(self):
        super().__init__()
        self.language_model = _TextModel()
        self.vision_tower = _Container(nn.Linear(4, 4))  # sin bloque en G1


class FakeGemma3n(nn.Module):
    def __init__(self):
        super().__init__()
        self.model = _Inner()
        self.lm_head = nn.Linear(256, 1000, bias=False)

    @property
    def dtype(self):  # PreTrainedModel la expone; el fake la imita
        return torch.float32


def _adapter():
    cfg = make_gemma3n_cfg()
    reg = BlockRegistry.from_config(cfg, 4)  # fp32 → dtype_bytes=4
    obs = Observer()
    model = FakeGemma3n()
    return Adapter(model, reg, obs), obs, model


def test_ple_fanout_un_disparo_por_capa():
    adapter, obs, model = _adapter()
    run_cfg = RunConfig(model_id="fake", prompts_file="x", prompt_hash="x")
    with obs.capture(run_cfg):
        adapter.instrument()
        ids = torch.tensor([[1, 2, 3, 4, 5]])  # 5 tokens en el paso
        model.model.language_model.embed_tokens_per_layer(ids)
        adapter.remove()
        events = [e for e in obs.trace.events
                  if e.event_type == "LOGICAL_ACCESS"]
    # 1 disparo del módulo único → 3 capas × 1 LOGICAL_ACCESS
    assert sorted(e.block_id for e in events) == ["L0.ple", "L1.ple", "L2.ple"]
    assert all(e.component == "PLE" for e in events)
    # tamaño de FILA: n_tokens × ple_dim × dtype_bytes = 5·64·4 = 1280
    assert all(e.size_bytes == 5 * 64 * 4 for e in events)


def test_embed_tamano_fila_con_registry_cfg():
    # gemma3n: model.config es el wrapper (sin hidden_size) — el tamaño de
    # fila debe salir de registry.cfg (text config), no del modelo
    adapter, obs, model = _adapter()
    run_cfg = RunConfig(model_id="fake", prompts_file="x", prompt_hash="x")
    with obs.capture(run_cfg):
        adapter.instrument()
        ids = torch.tensor([[7]])
        model.model.language_model.embed_tokens(ids)
        adapter.remove()
        evs = [e for e in obs.trace.events
               if e.event_type == "LOGICAL_ACCESS"]
    assert [e.block_id for e in evs] == ["embed"]
    assert evs[0].size_bytes == 256 * 4  # hidden × dtype_bytes


def test_aug_y_ffn_mapean_al_container():
    adapter, obs, model = _adapter()
    run_cfg = RunConfig(model_id="fake", prompts_file="x", prompt_hash="x")
    with obs.capture(run_cfg):
        adapter.instrument()
        x = torch.randn(1, 256)
        layer0 = model.model.language_model.layers[0]
        layer0.self_attn(x)
        layer0.mlp(x)
        layer0.laurel(x)
        adapter.remove()
        evs = {e.block_id for e in obs.trace.events
               if e.event_type == "LOGICAL_ACCESS"}
    assert evs == {"L0.attn", "L0.ffn", "L0.aug"}  # containers, no hijos
