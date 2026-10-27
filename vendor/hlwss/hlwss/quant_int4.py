"""Cuantización int4 propia para expertos fusionados de MoE (ADR-0063).

Los expertos de Qwen2MoE en transformers 5.x vienen FUSIONADOS: dos tensores
3D por capa (gate_up_proj [E,2I,H], down_proj [E,H,I]), no módulos Linear
individuales — por eso ni bitsandbytes ni torchao (que exige la librería
cerrada mslk para int4) los pueden cuantizar. Solución: formato int4 propio
group-wise (group=64, escala fp16 por grupo, nibbles empaquetados en uint8),
con dequant perezoso SOLO de los expertos seleccionados por el router.

Por qué importa el diseño: el dequant perezoso ES la semilla de la política
de residencia de Fase 1 — aquí solo dequantizamos lo que el modelo pide, que
es exactamente lo que una política expert-aware querría traer de SSD.

Uso: load_int4_moe(model_id, device) → modelo listo para instrumentar con
el Adapter (los nombres de módulo se preservan; el container Int4Experts
ocupa el mismo atributo `mlp.experts`, así el path fusionado de V2 sigue
funcionando).
"""

import json
import re
from pathlib import Path

import torch
import torch.nn.functional as F
from safetensors import safe_open
from transformers import AutoConfig, AutoModelForCausalLM

GROUP = 64  # group-wise a lo largo de la dimensión de entrada
BYTES_POR_PESO = 0.5 + 2 / GROUP  # nibble + escala fp16 por grupo


def pack_int4(w: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """w [..., I] bf16 → (packed uint8 [..., I/2], scales fp16 [..., I/GROUP]).

    Valores en nibble = round(w / escala) + 8 ∈ [0, 15], escala = absmax/7
    por grupo de GROUP columnas.
    """
    if w.shape[-1] % GROUP != 0:
        raise ValueError(f"I={w.shape[-1]} no múltiplo de GROUP={GROUP}")
    grupos = w.shape[-1] // GROUP
    wf = w.float().reshape(*w.shape[:-1], grupos, GROUP)
    escala_g = wf.abs().amax(dim=-1).div(7).clamp_min(1e-8)  # [..., grupos]
    q = torch.round(wf / escala_g[..., None]).clamp(-8, 7).to(torch.int8) + 8
    q = q.reshape(*w.shape[:-1], -1)  # [..., I]; pares (0,1),(2,3)… dentro de
    packed = (q[..., 0::2] | (q[..., 1::2] << 4)).to(torch.uint8)  # cada grupo
    return packed.contiguous(), escala_g.to(torch.float16).contiguous()


def dequant_int4(packed: torch.Tensor, scales: torch.Tensor) -> torch.Tensor:
    """Inversa de pack_int4: [..., I/2] uint8 + [..., I/G] fp16 → [..., I] bf16."""
    lo = (packed & 0x0F).to(torch.int16)
    hi = (packed >> 4).to(torch.int16)
    q = torch.stack([lo, hi], dim=-1).reshape(*packed.shape[:-1], -1) - 8
    grupos = scales.shape[-1]
    w = q.float().reshape(*q.shape[:-1], grupos, GROUP)
    w = (w * scales.float()[..., None]).reshape(*q.shape[:-1], -1)
    return w.to(torch.bfloat16)


class Int4Experts(torch.nn.Module):
    """Drop-in de Qwen2MoeExperts (transformers 5.x) con pesos int4.

    Misma signatura de forward; dequantiza por demanda solo los expertos
    seleccionados. Los pesos viven como buffers no persistentes → viajan con
    .to(device) como el resto del modelo.
    """

    def __init__(self, num_experts: int, num_experts_per_tok: int):
        super().__init__()
        self.num_experts = num_experts
        self.top_k = num_experts_per_tok
        self.act_fn = F.silu
        self.gate_up_proj = None   # buffers registrados en pack()
        self.down_proj = None

    def pack(self, name: str, w: torch.Tensor) -> None:
        """Recibe el tensor bf16 completo [E, O, I] y lo guarda como int4."""
        packed, scales = pack_int4(w.float().cpu())
        self.register_buffer(f"{name}_q", packed, persistent=False)
        self.register_buffer(f"{name}_s", scales, persistent=False)
        if name == "gate_up_proj":
            self.gate_up_shape = tuple(w.shape)
        else:
            self.down_shape = tuple(w.shape)

    def _dequant(self, name: str, idx: int) -> torch.Tensor:
        packed = getattr(self, f"{name}_q")[idx]
        scales = getattr(self, f"{name}_s")[idx]
        return dequant_int4(packed, scales)

    def forward(self, hidden_states, top_k_index, top_k_weights):
        final_hidden_states = torch.zeros_like(hidden_states)
        with torch.no_grad():
            expert_mask = F.one_hot(top_k_index, num_classes=self.num_experts)
            expert_mask = expert_mask.permute(2, 1, 0)
            expert_hit = torch.greater(
                expert_mask.sum(dim=(-1, -2)), 0).nonzero()
        for e in expert_hit:
            i = int(e[0])
            if i == self.num_experts:
                continue
            top_k_pos, token_idx = torch.where(expert_mask[i])
            current = hidden_states[token_idx]
            # .to(dtype de activación): el modelo puede correr en bf16/fp32
            gate_up = self._dequant("gate_up_proj", i).to(current.dtype)
            down = self._dequant("down_proj", i).to(current.dtype)
            gate, up = F.linear(current, gate_up).chunk(2, dim=-1)
            h = self.act_fn(gate) * up
            h = F.linear(h, down)
            h = h * top_k_weights[token_idx, top_k_pos, None]
            final_hidden_states.index_add_(
                0, token_idx, h.to(final_hidden_states.dtype))
        return final_hidden_states


def load_int4_moe(model_id: str, device: str) -> torch.nn.Module:
    """Carga un MoE HF cuantizando los expertos a int4 y dejando el resto en
    bf16. Streaming: un tensor bf16 en RAM a la vez (mmap desde safetensors).

    El checkpoint guarda expertos INDIVIDUALES (gate/up/down por experto,
    formato transformers <5.x) y el modelo meta los espera FUSIONADOS
    (gate_up_proj [E,2I,H]) — el loader fusiona por capa al cuantizar.

    Pico de RAM ≈ expertos bf16 de una capa (1.3GB) + stack + acumulado
    int4 (~6.6GB) + no-expertos bf16 (~3.6GB) ≈ 12GB."""
    cfg = AutoConfig.from_pretrained(model_id)
    with torch.device("meta"):
        model = AutoModelForCausalLM.from_config(cfg)
    for layer in model.model.layers:
        layer.mlp.experts = Int4Experts(cfg.num_experts,
                                        cfg.num_experts_per_tok)
    snapshot = Path(cfg._name_or_path)
    if not (snapshot / "model.safetensors.index.json").exists():
        from huggingface_hub import snapshot_download
        snapshot = Path(snapshot_download(model_id))
    index = json.loads((snapshot / "model.safetensors.index.json").read_text())
    weight_map = index["weight_map"]
    files: dict = {}

    def read(name: str) -> torch.Tensor:
        fname = weight_map[name]
        if fname not in files:
            files[fname] = safe_open(snapshot / fname, framework="pt")
        return files[fname].get_tensor(name)

    expert_key = re.compile(r"\.mlp\.experts\.\d+\.")
    try:
        # 1) expertos: individuales en checkpoint → fusionados + int4
        for i, layer in enumerate(model.model.layers):
            cont = layer.mlp.experts
            gate_ups, downs = [], []
            for e in range(cfg.num_experts):
                base = f"model.layers.{i}.mlp.experts.{e}"
                g = read(f"{base}.gate_proj.weight")
                u = read(f"{base}.up_proj.weight")
                gate_ups.append(torch.cat([g, u], dim=0))  # [2*moe_inter, H]
                downs.append(read(f"{base}.down_proj.weight"))
                del g, u
            cont.pack("gate_up_proj", torch.stack(gate_ups))
            cont.pack("down_proj", torch.stack(downs))
            del gate_ups, downs
        # 2) resto de los pesos: parámetros bf16 normales
        for name in weight_map:
            if expert_key.search(name):
                continue
            t = read(name)
            parent, attr = name.rsplit(".", 1)
            module = model.get_submodule(parent)
            if hasattr(module, attr) and isinstance(
                    getattr(module, attr), torch.nn.Parameter):
                module.register_parameter(
                    attr, torch.nn.Parameter(t, requires_grad=False))
            else:
                setattr(module, attr,
                        torch.nn.Parameter(t, requires_grad=False))
            del t
    finally:
        for f in files.values():
            f.__exit__(None, None, None)
    # defensivo: cualquier parámetro/buffer que haya quedado en meta (no estaba
    # en el checkpoint) se materializa en CPU — .to(device) no acepta meta
    n_meta = 0
    for name, p in list(model.named_parameters(remove_duplicate=False)):
        if p.is_meta:
            parent = model.get_submodule(name.rsplit(".", 1)[0])
            setattr(parent, name.rsplit(".", 1)[1],
                    torch.nn.Parameter(torch.zeros_like(p, device="cpu"),
                                       requires_grad=False))
            n_meta += 1
    for name, b in list(model.named_buffers()):
        if b.is_meta:
            parent = model.get_submodule(name.rsplit(".", 1)[0])
            parent.register_buffer(name.rsplit(".", 1)[1],
                                   torch.zeros_like(b, device="cpu"),
                                   persistent=False)
            n_meta += 1
    if n_meta:
        print(f"aviso: {n_meta} tensores meta materializados en cero (no estaban "
              f"en el checkpoint)")
    model.to(device)
    model.eval()
    return model
