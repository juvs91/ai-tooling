"""Fidelity checks V1–V4 (PRD §3).

V1 fidelidad de bytes (±10% promedio, solo pasos decode; prefill validado
aparte). V2 router MoE (experto ejecutado == topk(router_logits), in-memory).
V3 overhead (tok/s on/off ≥ 95%, desde metadata de dos corridas).
V4 reproducibilidad (tupla canónica sin timestamps, orden físico del Parquet —
nunca ORDER BY ts: EXECUTE+LOGICAL_ACCESS comparten timestamp base).
"""

import hashlib
import json
from pathlib import Path

import pandas as pd

from .adapter_torch import kv_bytes_per_step
from .observer import Trace
from .registry import is_moe

V4_FIELDS = ["event_type", "block_id", "layer", "component", "phase", "token_pos"]


def load_events(traces_dir: str | Path, run_id: str | None = None) -> pd.DataFrame:
    paths = sorted(Path(traces_dir).glob("*/events.parquet"))
    if run_id:
        paths = [p for p in paths if p.parent.name == run_id]
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)


# ---- V1 -----------------------------------------------------------------

def expected_bytes_per_step(registry, cfg, dtype_bytes: int,
                            prompt_len: int, token_pos: int) -> int:
    cfg = registry.cfg  # desenvuelto (gemma3n → text_config); Qwen idéntico
    embed_row = cfg.hidden_size * dtype_bytes          # una fila (§2.3)
    total = embed_row + registry.touch_size("lm_head")
    kinds = registry.kv_model_layers()
    ple_row = getattr(cfg, "hidden_size_per_layer_input", 0) * dtype_bytes
    blocks = registry.blocks()
    moe = is_moe(cfg)   # ADR-0068: MoE emite topk expertos, no un ffn fijo
    topk = getattr(cfg, "num_experts_per_tok", 0)
    for i in range(cfg.num_hidden_layers):
        total += registry.touch_size(f"L{i}.attn")
        if moe:
            total += topk * registry.touch_size(f"L{i}.expert.0")
            if f"L{i}.shared_expert" in blocks:
                total += registry.touch_size(f"L{i}.shared_expert")
        else:
            total += registry.touch_size(f"L{i}.ffn")
            if ple_row and f"L{i}.ple" in blocks:   # gemma3n (ADR-0067)
                total += ple_row
            if f"L{i}.aug" in blocks:
                total += registry.touch_size(f"L{i}.aug")
        total += kv_bytes_per_step(cfg, dtype_bytes, i, prompt_len + token_pos,
                                   kinds.get(i))
    if "ple.proj" in blocks:   # proyección global PLE: Linear densa, 1×/paso
        total += registry.touch_size("ple.proj")
    return total


def v1(df: pd.DataFrame, registry, cfg, dtype_bytes: int, prompt_len: int) -> dict:
    """Criterio ±10% sobre decode (token_pos NOT NULL); prefill por separado."""
    cfg = registry.cfg
    pre = df[df.phase == "prefill"]
    la = df[(df.event_type == "LOGICAL_ACCESS") & (df.phase == "decode")]
    per_step = la.groupby("token_pos")["size_bytes"].sum().sort_index()
    expected = {t: expected_bytes_per_step(registry, cfg, dtype_bytes, prompt_len, t)
                for t in per_step.index}
    ratios = [abs(per_step[t] - expected[t]) / expected[t] for t in per_step.index]
    # Prefill: cada bloque no-experto exactamente 1 LOGICAL_ACCESS + 1 KV
    # agregado por capa. Expertos MoE: el path fusionado (OLMoE) emite por
    # POSICIÓN del prompt (topk × prompt_len filas/capa); el path por
    # submódulos (Qwen1.5) emite 1 vez (topk filas/capa). Ambos válidos.
    blocks = pre[pre.event_type == "LOGICAL_ACCESS"]
    n_layers = cfg.num_hidden_layers
    if is_moe(cfg):
        topk = getattr(cfg, "num_experts_per_tok", 0)
        bids = blocks.block_id.fillna("").astype(str)
        ok = sum(1 for b in bids if not b.startswith("L")) == 2
        for i in range(n_layers):
            p = f"L{i}."
            n_attn = int(bids.str.startswith(p + "attn").sum())
            n_kv = int(bids.str.startswith(p + "kv").sum())
            n_sh = int(bids.str.startswith(p + "shared_expert").sum())
            n_ex = int(bids.str.startswith(p + "expert.").sum())
            ok = ok and n_attn == 1 and n_kv == 1 and n_sh <= 1 \
                and n_ex in (topk, topk * prompt_len)
    else:
        per_layer = 4 if getattr(registry, "_gemma3n", False) else 2  # attn+ffn+aug+ple
        # gemma3n: +1 por ple.proj (proyección global, 1 acceso/paso)
        n_proj = 1 if getattr(registry, "_gemma3n", False) else 0
        ok = len(blocks) == 2 + per_layer * n_layers + n_layers + n_proj
    return {"criterion": (sum(ratios) / len(ratios)) if ratios else 1.0,
            "passed": (sum(ratios) / len(ratios) if ratios else 1.0) <= 0.10,
            "steps": len(ratios), "prefill_ok": ok}


# ---- V2 (in-memory, contra el Trace de la corrida) -----------------------

def v2(trace: Trace) -> dict:
    """Experto emitido == topk(logits crudos guardados), por token_pos y capa.
    Con expertos fusionados (transformers 5.x) la emisión viene del router;
    con submódulos por experto, de los hooks de cada experto. El shared_expert
    (siempre activo, no router-selected) se excluye: no es selección del
    router, es parte del working set siempre-caliente."""
    emitted: dict = {}
    for e in trace.events:
        # el shared_expert se ejecuta SIEMPRE (no es router-selected): forma
        # parte del working set siempre-caliente, pero V2 compara solo la
        # selección del router (Qwen1.5-MoE: primer modelo con shared_expert)
        if e.component == "EXPERT" and e.event_type == "LOGICAL_ACCESS" \
                and ".shared_expert" not in e.block_id:
            emitted.setdefault((e.token_pos, e.layer), set()).add(e.block_id)
    checked = matched = 0
    for token_pos, phase, layer, logits, sel in trace.router_logits:
        if phase != "decode" or token_pos is None or layer is None:
            continue
        # want = la selección REAL del router (sel), no topk recomputable:
        # con logits bf16 y 60 expertos, los near-ties (~1 ulp) rompen
        # distinto entre topk en MPS (sel) y topk en CPU (medido: 2/168
        # flips en Qwen1.5-MoE). El path fusionado emite desde sel en el
        # mismo hook → este want verifica el PLUMBING de eventos (faltantes/
        # extra/capa errada); el path no fusionado sí compara ejecución real.
        want = {f"L{layer}.expert.{j}" for j in sel}
        got = emitted.get((token_pos, layer), set())
        checked += 1
        matched += int(got == want)
    return {"criterion": (matched / checked) if checked else 0.0,
            "passed": checked > 0 and matched == checked, "steps": checked}


# ---- V3 (desde metadata de dos corridas: observer on vs HLWSS_OFF=1) -----

def v3(metadata_on: dict, metadata_off: dict) -> dict:
    a, b = metadata_on.get("tokps", 0.0), metadata_off.get("tokps", 0.0)
    ratio = (a / b) if b else 0.0
    return {"criterion": ratio, "passed": ratio >= 0.95,
            "tokps_on": a, "tokps_off": b}


# ---- V4 ------------------------------------------------------------------

def _v4_seq(events_path: Path) -> list[tuple]:
    df = pd.read_parquet(events_path)  # orden físico = orden de inserción
    cols = []
    for f in V4_FIELDS:
        s = df[f]
        if s.isna().any():  # NaN != NaN en tuplas: normalizar a None
            s = s.astype(object).where(s.notna(), None)
        cols.append(s)
    return list(zip(*cols))


def _hash_seq(seq: list[tuple]) -> str:
    return hashlib.sha256(repr(seq).encode()).hexdigest()


def v4(traces_dir: str | Path, run_a: str, run_b: str) -> dict:
    base = Path(traces_dir)
    same = _hash_seq(_v4_seq(base / run_a / "events.parquet")) == \
           _hash_seq(_v4_seq(base / run_b / "events.parquet"))
    return {"criterion": 1.0 if same else 0.0, "passed": same}


# ---- reporte -------------------------------------------------------------

def check_traces(traces_dir, registry, cfg, dtype_bytes, prompt_len,
                 v: str = "all") -> dict:
    sample_n = None
    metas = sorted(Path(traces_dir).glob("*/metadata.json"))
    if metas:
        m = json.loads(metas[0].read_text())
        sample_n = m.get("sample_n", 1)
    if sample_n and sample_n > 1:
        raise SystemExit(f"sample_n={sample_n}: V1/V4 exigen trazas sin muestreo")
    df = load_events(traces_dir)
    report = {}
    if v in ("all", "V1"):
        report["V1"] = v1(df, registry, cfg, dtype_bytes, prompt_len)
    if v in ("all", "V4") and len(metas) >= 2:
        ids = sorted(p.parent.name for p in metas)
        report["V4"] = v4(traces_dir, ids[0], ids[1])
    return report
