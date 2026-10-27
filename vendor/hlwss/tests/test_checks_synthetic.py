"""Tests de checks sobre trazas sintéticas (sin modelo): V1, prefill, V4, V2."""

from types import SimpleNamespace

import pandas as pd

from hlwss.adapter_torch import kv_bytes_per_step
from hlwss.checks import v1, v2, v4
from hlwss.events import Event
from hlwss.observer import Observer, RunConfig, Trace
from hlwss.writer import write


def make_cfg(n_layers=2, hidden=64, heads=4, kv_heads=2, inter=128,
             vocab=1000, tie=True):
    return SimpleNamespace(
        hidden_size=hidden, vocab_size=vocab, tie_word_embeddings=tie,
        sliding_window=None, num_hidden_layers=n_layers,
        num_attention_heads=heads, num_key_value_heads=kv_heads,
        intermediate_size=inter, max_position_embeddings=512)


def _reg(cfg, d=2):
    from hlwss.registry import BlockRegistry
    return BlockRegistry.from_config(cfg, d)


def _synth_trace(cfg, registry, run_id, prompt_len=8, steps=4):
    """Emula la emisión del adapter para un modelo toy de n capas."""
    obs = Observer()
    cfg_run = RunConfig(model_id="toy", prompts_file="p", prompt_hash="h",
                        max_tokens=steps, capture_distributions=False)
    trace = Trace(run_id=run_id, cfg=cfg_run)
    obs._trace = trace
    d = 2
    blocks = [("embed", None, "EMBED")] + [("lm_head", None, "LM_HEAD")]
    for i in range(cfg.num_hidden_layers):
        blocks += [(f"L{i}.attn", i, "ATTN"), (f"L{i}.ffn", i, "FFN")]

    def emit(phase, t, ctx):
        ts = obs.now_ns()
        for bid, layer, comp in blocks:
            size = cfg.hidden_size * d if bid == "embed" else registry.touch_size(bid)
            for et in ("EXECUTE", "LOGICAL_ACCESS"):
                obs.emit(Event(ts, run_id, t, phase, layer, comp, bid, et, size, 100, "hook"))
        for i in range(cfg.num_hidden_layers):
            from hlwss.adapter_torch import kv_bytes_per_step
            obs.emit(Event(ts, run_id, t, phase, i, "KV", f"L{i}.kv",
                           "LOGICAL_ACCESS", kv_bytes_per_step(cfg, d, i, ctx), None, "derived"))
    emit("prefill", None, prompt_len)
    for t in range(1, steps + 1):
        emit("decode", t, prompt_len + t)
    return trace


# ---- MoE (ADR-0068): V1 MoE-aware, estilo OLMoE (sin shared_expert) -------

def make_moe_cfg(**over):
    """OLMoE-style: expertos con intermediate_size (sin moe_intermediate_size
    en el config — el registry cae al fallback), sin shared_expert, topk=2."""
    base = dict(hidden_size=64, vocab_size=1000, tie_word_embeddings=False,
                sliding_window=None, num_hidden_layers=2,
                num_attention_heads=4, num_key_value_heads=2,
                intermediate_size=32, max_position_embeddings=512,
                num_experts=8, num_experts_per_tok=2)
    base.update(over)
    return SimpleNamespace(**base)


def _synth_moe_trace(cfg, registry, run_id, prompt_len=8, steps=4,
                     prefill_per_position=False):
    """Emula el path fusionado del adapter: el router emite topk expertos.
    Con `prefill_per_position` emula OLMoE real: en prefill el módulo
    fusionado ejecuta expertos por CADA posición del prompt."""
    obs = Observer()
    cfg_run = RunConfig(model_id="moe-toy", prompts_file="p", prompt_hash="h",
                        max_tokens=steps, capture_distributions=False)
    trace = Trace(run_id=run_id, cfg=cfg_run)
    obs._trace = trace
    d = 2
    sel = [[0, 3], [1, 4]]  # selección por capa (determinista)

    def emit(phase, t, ctx, with_head=True, with_kv=True,
             with_experts=True, with_attn=True):
        ts = obs.now_ns()
        if with_head:
            for bid, layer, comp in [("embed", None, "EMBED"),
                                     ("lm_head", None, "LM_HEAD")]:
                size = cfg.hidden_size * d if bid == "embed" \
                    else registry.touch_size(bid)
                for et in ("EXECUTE", "LOGICAL_ACCESS"):
                    obs.emit(Event(ts, run_id, t, phase, layer, comp, bid, et,
                                   size, 100, "hook"))
        for i in range(cfg.num_hidden_layers):
            pairs = []
            if with_attn:
                pairs.append((f"L{i}.attn", i, "ATTN"))
            if with_experts:
                pairs += [(f"L{i}.expert.{j}", i, "EXPERT") for j in sel[i]]
            if f"L{i}.shared_expert" in registry.blocks():
                pairs.append((f"L{i}.shared_expert", i, "FFN"))
            for bid, layer, comp in pairs:
                for et in ("EXECUTE", "LOGICAL_ACCESS"):
                    obs.emit(Event(ts, run_id, t, phase, layer, comp, bid, et,
                                   registry.touch_size(bid), 100, "hook"))
            if with_kv:
                obs.emit(Event(ts, run_id, t, phase, i, "KV", f"L{i}.kv",
                               "LOGICAL_ACCESS",
                               kv_bytes_per_step(cfg, d, i, ctx), None, "derived"))
    if prefill_per_position:
        emit("prefill", None, prompt_len, with_experts=False)
        for _ in range(prompt_len):  # solo expertos, por cada posición
            emit("prefill", None, prompt_len, with_head=False, with_kv=False,
                 with_attn=False)
    else:
        emit("prefill", None, prompt_len)
    for t in range(1, steps + 1):
        emit("decode", t, prompt_len + t)
    return trace


def test_v1_moe_olmoe_style_passes(tmp_path):
    cfg = make_moe_cfg()
    reg = _reg(cfg)
    tr = _synth_moe_trace(cfg, reg, "m1")
    _write(tmp_path, tr, reg)
    df = pd.read_parquet(tmp_path / "m1" / "events.parquet")
    r = v1(df, reg, cfg, 2, prompt_len=8)
    assert r["passed"], r
    assert r["prefill_ok"]
    assert r["criterion"] <= 1e-9


def test_v1_moe_con_shared_expert_pasa(tmp_path):
    # regresión Qwen1.5-style: shared_expert siempre activo → +1 por capa
    cfg = make_moe_cfg(shared_expert_intermediate_size=32)
    reg = _reg(cfg)
    tr = _synth_moe_trace(cfg, reg, "m2")
    _write(tmp_path, tr, reg)
    df = pd.read_parquet(tmp_path / "m2" / "events.parquet")
    r = v1(df, reg, cfg, 2, prompt_len=8)
    assert r["passed"], r
    assert r["prefill_ok"]


def test_v1_moe_prefill_fusionado_por_posicion_pasa(tmp_path):
    # OLMoE real (Exp1, ADR-0068): en prefill el módulo de expertos fusionado
    # se ejecuta por CADA posición del prompt → topk × prompt_len filas
    # EXPERT por capa (no topk). V1 debe aceptar ambos patrones.
    cfg = make_moe_cfg()
    reg = _reg(cfg)
    tr = _synth_moe_trace(cfg, reg, "m4", prefill_per_position=True)
    _write(tmp_path, tr, reg)
    df = pd.read_parquet(tmp_path / "m4" / "events.parquet")
    r = v1(df, reg, cfg, 2, prompt_len=8)
    assert r["passed"], r
    assert r["prefill_ok"]


def test_v1_moe_detecta_expertos_faltantes(tmp_path):
    cfg = make_moe_cfg()
    reg = _reg(cfg)
    tr = _synth_moe_trace(cfg, reg, "m3")
    # sin expertos en ninguna capa: rompe el conteo de prefill y la razón
    # de bytes (los expertos son ~25% del total por paso en este toy)
    tr.events = [e for e in tr.events if e.component != "EXPERT"]
    _write(tmp_path, tr, reg)
    df = pd.read_parquet(tmp_path / "m3" / "events.parquet")
    r = v1(df, reg, cfg, 2, prompt_len=8)
    assert not r["prefill_ok"]
    assert not r["passed"]



def _write(tmp_path, trace, registry):
    meta = {"event_columns": [], "run_id": trace.run_id}
    return write(trace, registry.sidecar(), meta, tmp_path)


def test_v1_perfect_trace_passes(tmp_path):
    cfg = make_cfg()
    reg = _reg(cfg)
    tr = _synth_trace(cfg, reg, "r1")
    _write(tmp_path, tr, reg)
    df = pd.read_parquet(tmp_path / "r1" / "events.parquet")
    r = v1(df, reg, cfg, 2, prompt_len=8)
    assert r["passed"], r
    assert r["prefill_ok"]
    assert r["criterion"] <= 1e-9  # traza sintética perfecta: 0% desviación


def test_v1_detects_missing_bytes(tmp_path):
    cfg = make_cfg()
    reg = _reg(cfg)
    tr = _synth_trace(cfg, reg, "r2")
    tr.events = [e for e in tr.events if e.block_id != "L0.ffn"]
    _write(tmp_path, tr, reg)
    df = pd.read_parquet(tmp_path / "r2" / "events.parquet")
    r = v1(df, reg, cfg, 2, prompt_len=8)
    assert not r["passed"]


def test_v4_equal(tmp_path):
    cfg = make_cfg()
    reg = _reg(cfg)
    _write(tmp_path, _synth_trace(cfg, reg, "a"), reg)
    _write(tmp_path, _synth_trace(cfg, reg, "b"), reg)
    assert v4(tmp_path, "a", "b")["passed"]  # mismas emisiones → mismo hash


def test_v4_diff(tmp_path):
    cfg = make_cfg()
    reg = _reg(cfg)
    _write(tmp_path, _synth_trace(cfg, reg, "a", steps=4), reg)
    _write(tmp_path, _synth_trace(cfg, reg, "z", steps=5), reg)  # secuencia distinta
    assert not v4(tmp_path, "a", "z")["passed"]


def test_v4_ignores_sizes_and_timestamps(tmp_path):
    # prompt_len distinto cambia size_bytes de KV pero NO la tupla V4:
    # la secuencia de emisión es idéntica → V4 debe pasar (es lo correcto).
    cfg = make_cfg()
    reg = _reg(cfg)
    _write(tmp_path, _synth_trace(cfg, reg, "a", prompt_len=8), reg)
    _write(tmp_path, _synth_trace(cfg, reg, "z", prompt_len=9), reg)
    assert v4(tmp_path, "a", "z")["passed"]


def test_v2_router_vs_executed():
    import torch
    cfg_run = RunConfig(model_id="moe", prompts_file="p", prompt_hash="h")
    trace = Trace(run_id="m", cfg=cfg_run)
    lg1 = torch.tensor([[0.1, 0.9, 0.3, 0.2, 0.8, 0.0, 0.05, 0.4]])  # top2: 1,4
    lg2 = torch.tensor([[0.7, 0.2, 0.6, 0.1, 0.0, 0.3, 0.5, 0.4]])  # top2: 0,2
    trace.router_logits = [
        (1, "decode", 0, lg1, [1, 4]),
        (2, "decode", 0, lg2, [0, 2]),
    ]
    trace.events = [
        Event(0, "m", 1, "decode", 0, "EXPERT", "L0.expert.1", "LOGICAL_ACCESS", 1, None, "hook"),
        Event(0, "m", 1, "decode", 0, "EXPERT", "L0.expert.4", "LOGICAL_ACCESS", 1, None, "hook"),
        Event(0, "m", 2, "decode", 0, "EXPERT", "L0.expert.0", "LOGICAL_ACCESS", 1, None, "hook"),
        Event(0, "m", 2, "decode", 0, "EXPERT", "L0.expert.2", "LOGICAL_ACCESS", 1, None, "hook"),
    ]
    assert v2(trace)["passed"]
    trace.events = trace.events[:1]  # emitido != topk(logits)
    assert not v2(trace)["passed"]
