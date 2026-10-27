"""Simulator de políticas de residencia (Fase 0.7, ADR-0065; Fase 1
preflight, ADR-0066).

Replay del stream LOGICAL_ACCESS de una traza real bajo 4 políticas: lru
(piso), hlwss (pin estático + SLRU, ADR-0065), hlwss-adaptive (Fase 1:
detección de régimen FIT + pin shrink-to-fit, ADR-0066) y oracle (techo,
Belady). Responde la pregunta de la tesis: ¿cuántos bytes deja de
transferir una política model-aware vs el LRU del OS, y qué tan cerca queda
del óptimo?

Ground truth de tamaños: registry.blocks del metadata (NUNCA size_bytes del
evento, que mide bytes tocados). KV paginado (L{i}.kv.p{j}) con tamaño de
kv_pages_template. Bloques con size_bytes==0 (ties, p.ej. lm_head) excluidos:
residentes gratuitos.
"""

import json
import re
from collections import OrderedDict
from pathlib import Path

import pandas as pd

POLICIES = ("lru", "hlwss", "hlwss-adaptive", "oracle")
_EXPERT = re.compile(r"^L\d+\.expert\.\d+$")
_KV_PAGE = re.compile(r"^L\d+\.kv\.p\d+$")
_INF = float("inf")
_HOT_FRAC = 0.8    # reparto SLRU del budget no-pinneado: 80% hot / 20% cold
_COVERAGE = 0.9    # target de cobertura del pin sobre accesos EXPERT
_PIN_BUDGET_FRAC = 0.9  # fracción máxima del budget cedida al pin (ADR-0066)


def load_run(traces, run_dir):
    """(stream, blocks, kv_page_bytes, meta) desde un directorio de traza.

    stream: block_id en orden temporal (LOGICAL_ACCESS, no nulos).
    blocks: block_id → size_bytes desde registry.blocks (ties 0 excluidos).
    """
    d = Path(traces) / run_dir
    meta = json.loads((d / "metadata.json").read_text())
    df = pd.read_parquet(d / "events.parquet")
    la = df[(df.event_type == "LOGICAL_ACCESS") & df.block_id.notna()]
    stream = la.sort_values("timestamp_ns", kind="stable").block_id.tolist()
    reg = meta.get("registry") or {}
    blocks = {bid: int(b["size_bytes"]) for bid, b in
              (reg.get("blocks") or {}).items() if b.get("size_bytes")}
    kv = (reg.get("kv_pages_template") or {}).get("kv_page_bytes")
    return stream, blocks, int(kv) if kv else None, meta


def _block_size(blocks, kv_page_bytes, bid):
    if bid in blocks:
        return blocks[bid]
    if kv_page_bytes and _KV_PAGE.match(bid):
        return kv_page_bytes
    return None


def scale_blocks(blocks_meta, factors):
    """ADR-0068 (G2): size_bytes reescalados por familia para escenarios de
    cuantización. `blocks_meta` = registry.blocks del metadata (conserva
    `component` y `derived`); `factors` = {componente: factor}; la clave
    `weights` aplica a todo bloque no derivado salvo que el componente tenga
    factor explícito (este gana). KV queda fuera (se pagina aparte)."""
    if not factors:
        return {bid: int(b["size_bytes"]) for bid, b in blocks_meta.items()
                if b.get("size_bytes")}
    factors = {k.upper(): v for k, v in factors.items()}  # cli.py manda MAYÚSCULAS
    out = {}
    for bid, b in blocks_meta.items():
        size = b.get("size_bytes")
        if not size:
            continue
        f = factors.get(b.get("component", ""), 1.0)
        if "WEIGHTS" in factors and not b.get("derived") and f == 1.0:
            f = factors["WEIGHTS"]
        out[bid] = int(size * f)
    return out


def _pinned(blocks, stream, coverage=_COVERAGE, pin_cap=None):
    """Bloques pinneados (política hlwss): no-EXPERT tocados (incl.
    shared_expert) + expertos top-frecuencia cubriendo `coverage` de los
    accesos EXPERT. Solo bloques presentes en el stream: pinneando un bloque
    que el modelo nunca tocó (p.ej. ffn denso fantasma en un MoE) se inflaría
    el presupuesto con bytes que no existen en la realidad.

    `pin_cap` (bytes, ADR-0066): presupuesto máximo del pin (shrink-to-fit).
    La selección de expertos se detiene al cruzarlo, dejando la cobertura
    efectiva por debajo del target antes que exceder el budget."""
    touched = set(stream)
    pin = {b for b in touched if b in blocks
           and (not _EXPERT.match(b) or ".shared_expert" in b)}
    base_bytes = sum(blocks[b] for b in pin)
    if pin_cap is not None and base_bytes > pin_cap:
        return pin  # la base no-expert no cabe: infeasible, sin expertos
    freq: dict = {}
    n_expert = 0
    for bid in stream:
        if _EXPERT.match(bid):
            freq[bid] = freq.get(bid, 0) + 1
            n_expert += 1
    covered = expert_bytes = 0
    for bid, _ in sorted(freq.items(), key=lambda kv: -kv[1]):
        if covered >= coverage * n_expert:
            break
        size = blocks.get(bid)
        if size is None:
            continue
        if pin_cap is not None and expert_bytes + size > pin_cap - base_bytes:
            break
        pin.add(bid)
        expert_bytes += size
        covered += freq[bid]
    return pin


def _unique_touched_bytes(blocks, stream, kv_page_bytes):
    """Bytes de los bloques distintos tocados (señal de régimen FIT, ADR-0066:
    si caben en el budget, pin+SLRU es overhead puro y se degrada a LRU)."""
    seen = set()
    total = 0
    for bid in stream:
        if bid in seen:
            continue
        size = _block_size(blocks, kv_page_bytes, bid)
        if size is not None:
            seen.add(bid)
            total += size
    return total


def _run_lru(touches, budget):
    """LRU puro. touches: [(bid, size)] en orden. Devuelve contadores."""
    resident = OrderedDict()  # bid → size; orden = LRU→MRU
    used = hits = transferred = evictions = peak = 0
    for bid, size in touches:
        if bid in resident:
            hits += 1
            resident.move_to_end(bid)
            continue
        transferred += size
        resident[bid] = size
        used += size
        while used > budget and resident:
            used -= resident.popitem(last=False)[1]
            evictions += 1
        peak = max(peak, used)
    return hits, transferred, evictions, peak


def _run_slru(touches, budget, pinned):
    """SLRU sobre el budget no-pinneado; pinned cuentan como hit directo."""
    hot_cap = int(_HOT_FRAC * budget)
    cold_cap = budget - hot_cap
    hot = OrderedDict()
    cold = OrderedDict()
    hot_used = cold_used = 0
    hits = transferred = evictions = peak = 0
    loaded = set()

    def shrink_cold():
        nonlocal cold_used, evictions
        while cold_used > cold_cap and cold:
            cold_used -= cold.popitem(last=False)[1]
            evictions += 1

    for bid, size in touches:
        if bid in pinned:
            # el primer toque del pinneado ES la carga en frío (miss, ya
            # contada en pin_bytes); solo los siguientes son hit
            if bid in loaded:
                hits += 1
            else:
                loaded.add(bid)
        elif bid in hot:
            hits += 1
            hot.move_to_end(bid)
        elif bid in cold:
            hits += 1
            cold.pop(bid)
            cold_used -= size
            hot[bid] = size
            hot_used += size
            while hot_used > hot_cap and hot:  # demote LRU de hot a cold
                eb, es = hot.popitem(last=False)
                hot_used -= es
                cold[eb] = es
                cold_used += es
                shrink_cold()
        else:
            transferred += size
            cold[bid] = size
            cold_used += size
            shrink_cold()
        peak = max(peak, hot_used + cold_used)
    return hits, transferred, evictions, peak


def _run_oracle(touches, budget):
    """Belady: evict el residente cuyo próximo uso sea más lejano (techo)."""
    seq = [bid for bid, _ in touches]
    nxt = [_INF] * len(seq)
    last = {}
    for i in range(len(seq) - 1, -1, -1):
        nxt[i] = last.get(seq[i], _INF)
        last[seq[i]] = i
    nxt_of: dict = {}
    resident = OrderedDict()
    used = hits = transferred = evictions = peak = 0
    for i, (bid, size) in enumerate(touches):
        nxt_of[bid] = nxt[i]
        if bid in resident:
            hits += 1
            continue
        transferred += size
        resident[bid] = size
        used += size
        while used > budget and resident:
            far = max(resident, key=lambda b: nxt_of[b])
            used -= resident.pop(far)
            evictions += 1
        peak = max(peak, used)
    return hits, transferred, evictions, peak


def _expert_coverage(pinned, stream):
    """Fracción de accesos EXPERT que caen en bloques pinneados (cobertura
    efectiva; el target es _COVERAGE, shrink-to-fit puede dejarla menor)."""
    n_hit = n_tot = 0
    for bid in stream:
        if _EXPERT.match(bid):
            n_tot += 1
            n_hit += bid in pinned
    return n_hit / n_tot if n_tot else None


def simulate(stream, blocks, kv_page_bytes, policy, budget_bytes):
    """Métricas de una política sobre un stream. budget_bytes incluye pin."""
    touches = []
    n_unknown = 0
    for bid in stream:
        size = _block_size(blocks, kv_page_bytes, bid)
        if size is None:
            n_unknown += 1  # nunca inferir tamaños de eventos
            continue
        touches.append((bid, size))
    pinned = set()
    regime = coverage = None
    runner = policy
    if policy == "hlwss":
        pinned = _pinned(blocks, stream)
    elif policy == "hlwss-adaptive":
        if _unique_touched_bytes(blocks, stream, kv_page_bytes) <= budget_bytes:
            # régimen FIT (ADR-0066): el WS único cabe; pin+SLRU es overhead
            # puro (cold-load + thrashing del cold segment) → LRU puro
            runner, regime = "lru", "fit"
        else:
            regime = "spill"
            pinned = _pinned(blocks, stream,
                             pin_cap=int(_PIN_BUDGET_FRAC * budget_bytes))
            coverage = _expert_coverage(pinned, stream)
    # el pin paga su carga en frío al t=0: residente no es gratuito, son
    # bytes que alguien transfirió una vez (sin esto la política "gana" por
    # artefacto contable, no por diseño)
    pin_bytes = sum(blocks[b] for b in pinned if b in blocks)
    infeasible = bool(pinned) and pin_bytes > budget_bytes
    if runner == "oracle":
        r = _run_oracle(touches, budget_bytes)
    elif runner in ("hlwss", "hlwss-adaptive"):
        r = _run_slru(touches, max(budget_bytes - pin_bytes, 0), pinned)
    else:
        r = _run_lru(touches, budget_bytes)
    hits, transferred, evictions, peak = r
    transferred += pin_bytes
    return {
        "policy": policy,
        "budget_bytes": budget_bytes,
        "touches": len(touches),
        "n_unknown": n_unknown,
        "bytes_transferred": transferred,
        "ram_hit_fraction": hits / len(touches) if touches else 0.0,
        "n_evictions": evictions,
        "resident_peak_bytes": peak,
        "pinned_bytes": pin_bytes,
        "infeasible": infeasible,
        "regime": regime,
        "coverage": coverage,
    }
