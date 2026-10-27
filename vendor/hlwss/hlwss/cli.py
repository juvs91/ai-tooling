"""HLWSS CLI — subcomandos run / check / analyze / sim / engine (ADR-0069).

run: carga modelo, instrumenta, vuelca traza. check: V1/V3/V4 contra trazas.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import torch

from .adapter_torch import Adapter
from .analyze import report as analyze_report
from .checks import v1, v3, v4
from .early_exit import EarlyExitProbe
from .observer import Observer, RunConfig
from .registry import BlockRegistry
from .writer import build_metadata, write

class ToyTokenizer:
    """Tokenizer determinista para el MoE sintético (nada que descargar)."""

    def __init__(self, vocab_size: int):
        self.vocab_size = vocab_size

    def encode(self, text: str) -> list[int]:
        return [1 + (hash(w) % (self.vocab_size - 1)) for w in text.split()] or [0]


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _metas(traces, model: str | None = None):
    """Itera (run_dir, metadata) de trazas, opcionalmente filtradas por modelo."""
    for mp in sorted(Path(traces).glob("*/metadata.json")):
        m = json.loads(mp.read_text())
        if model and m.get("model_id") != model:
            continue
        yield mp.parent, m


def cmd_run(a) -> int:
    prompts_data = json.loads(Path(a.prompts).read_text())
    prompt_text = prompts_data["prompts"][a.prompt_idx]

    torch.manual_seed(42)
    device = a.device or ("mps" if torch.backends.mps.is_available() else "cpu")
    if a.synthetic_moe:
        from transformers import Qwen3MoeConfig, Qwen3MoeForCausalLM
        cfg = Qwen3MoeConfig(
            vocab_size=1024, hidden_size=256, intermediate_size=512,
            num_hidden_layers=4, num_attention_heads=8, num_key_value_heads=4,
            num_experts=8, num_experts_per_tok=2, moe_intermediate_size=256,
            max_position_embeddings=1024, tie_word_embeddings=False,
        )
        model = Qwen3MoeForCausalLM(cfg)
        tokenizer = ToyTokenizer(cfg.vocab_size)
        model_id = "synthetic-qwen3moe-tiny"
    elif a.int4_experts:
        # MoE real con expertos fusionados → int4 propio (ADR-0063):
        # resto del modelo en bf16, streaming desde safetensors
        from .quant_int4 import load_int4_moe
        from transformers import AutoConfig, AutoTokenizer
        cfg = AutoConfig.from_pretrained(a.model)
        model = load_int4_moe(a.model, device)
        tokenizer = AutoTokenizer.from_pretrained(a.model)
        model_id = a.model
    else:
        from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
        cfg = AutoConfig.from_pretrained(a.model)
        if getattr(cfg, "model_type", "") == "gemma3n":
            # ADR-0067: Gemma3nForCausalLM = text-only del checkpoint multimodal
            # sin towers (vision exige timm). Módulos bajo model.* (sin language_model.)
            from transformers import Gemma3nForCausalLM
            model = Gemma3nForCausalLM.from_pretrained(
                a.model, torch_dtype="auto")
        else:
            model = AutoModelForCausalLM.from_pretrained(a.model, torch_dtype="auto")
        tokenizer = AutoTokenizer.from_pretrained(a.model)
        model_id = a.model

    model.to(device)
    model.eval()

    dtype_bytes = a.dtype_bytes or model.dtype.itemsize
    registry = BlockRegistry.from_config(
        cfg, dtype_bytes, a.kv_page_tokens,
        expert_dtype_bytes=a.expert_dtype_bytes or dtype_bytes)
    run_cfg = RunConfig(
        model_id=model_id, prompts_file=str(a.prompts),
        prompt_hash=sha256_file(a.prompts), seed=42, temperature=0.0,
        do_sample=False, max_tokens=a.max_tokens, device=device,
        sample_n=a.sample_n, capture_distributions=a.capture_distributions,
        kv_page_tokens=a.kv_page_tokens,
    )
    observer = Observer()
    adapter = Adapter(model, registry, observer)
    probe = None
    if a.early_exit_probe:
        probe = EarlyExitProbe(model)
        probe.attach()

    prompt_ids = tokenizer.encode(prompt_text)
    t0 = time.perf_counter()
    with observer.capture(run_cfg) as trace:
        adapter.instrument()
        gen = adapter.run(prompt_ids, a.max_tokens)
        adapter.remove()
    if probe is not None:
        probe.detach()
    elapsed = time.perf_counter() - t0

    sidecar = registry.sidecar()
    meta = build_metadata(trace, model_id, torch.__version__, sidecar)
    meta.update({
        "quant": str(model.dtype).replace("torch.", ""),
        "ssd_model_path": getattr(cfg, "_name_or_path", None) or model_id,
        "prompt_index": a.prompt_idx,
        "hooks": "off" if os.environ.get("HLWSS_OFF") == "1" else "on",
        "prompt_len": len(prompt_ids),
        "prompt_text_hash": hashlib.sha256(prompt_text.encode()).hexdigest(),
        "generated_tokens": len(gen),
        "elapsed_s": round(elapsed, 3),
        "tokps": round(len(gen) / elapsed, 2) if elapsed else 0.0,
    })
    if probe is not None:
        meta["early_exit"] = probe.report()
        fl = meta["early_exit"]["first_layer_at"]
        print(f"E1-A acuerdo final={meta['early_exit']['agreement_per_layer'][-1]} "
              f"primera capa ≥τ: {fl}")
    result = write(trace, sidecar, meta, a.out)

    print(f"run_id={trace.run_id} tokens={len(gen)} tokps={meta['tokps']} "
          f"events={len(trace.events)}")
    print(f"traza: {result.events_path}")
    if trace.router_logits:
        from .checks import v2
        r = v2(trace)
        print(f"V2 (MoE in-memory): {'OK' if r['passed'] else 'FALLA'} ({r['steps']} pasos)")
    return 0


def cmd_check(a) -> int:
    from transformers import AutoConfig
    meta_paths = sorted(Path(a.traces).glob("*/metadata.json"))
    if not meta_paths:
        print("sin trazas en", a.traces)
        return 1
    m0 = json.loads(meta_paths[0].read_text())
    cfg = AutoConfig.from_pretrained(m0["model_id"]) if not m0["model_id"].startswith("synthetic") else None

    report = {}
    if a.v in ("all", "V1"):
        if cfg is None:
            print("V1 requiere modelo descargable (no sintético)")
        else:
            reg = BlockRegistry.from_config(cfg, m0["registry"]["dtype_bytes"],
                                            m0.get("kv_page_tokens", 16))
            keep = [(d, m) for d, m in _metas(a.traces)
                    if m.get("prompt_index") == m0.get("prompt_index")
                    and m.get("hooks", "on") == "on"]
            # V1 por corrida y luego agregación: concatenar trazas del mismo
            # prompt duplicaría los token_pos solapados (t=1..16 aparece en
            # el smoke y en la corrida de 256) e inflaría los bytes ~2×
            per_run = [v1(pd.read_parquet(d / "events.parquet"), reg, cfg,
                          m0["registry"]["dtype_bytes"], m0["prompt_len"])
                       for d, m in keep]
            report["V1"] = {
                "criterion": sum(r["criterion"] for r in per_run) / len(per_run),
                "passed": all(r["passed"] and r["prefill_ok"] for r in per_run),
                "runs": len(per_run),
            }
    if a.v in ("all", "V3") and a.v3_pair:
        d_on, d_off = a.v3_pair.split(",")
        m_on = json.loads((Path(d_on) / "metadata.json").read_text())
        m_off = json.loads((Path(d_off) / "metadata.json").read_text())
        report["V3"] = v3(m_on, m_off)
    if a.v in ("all", "V4"):
        groups: dict = {}
        for d, m in _metas(a.traces):
            if m.get("hooks", "on") == "off":
                continue  # corrida baseline V3: sin eventos de bloques
            groups.setdefault(m.get("prompt_index", 0), []).append(
                (m.get("timestamp_utc", ""), d.name,
                 m.get("generated_tokens", 0)))
        for pid, runs in groups.items():
            runs.sort()  # cronológico por timestamp_utc, no por run_id
            ref_gen = runs[-1][2]
            # emparejar solo corridas comparables: mismo nº de tokens
            # generados (corta-distinta no es "irreproducible", es otra config)
            same = [r for r in runs if r[2] == ref_gen]
            if len(same) >= 2:
                report[f"V4(p{pid})"] = v4(a.traces, same[-2][1], same[-1][1])
    for k, r in report.items():
        mark = "OK" if r.get("passed") else "FALLA"
        print(f"{k}: {mark} criterion={r.get('criterion')}")
    return 0 if all(r.get("passed") for r in report.values()) else 1


def cmd_analyze(a) -> int:
    for d in sorted(Path(a.traces).glob("*/events.parquet")):
        print(analyze_report(a.traces, d.parent.name))
    return 0


def cmd_probe_ollama(a) -> int:
    """Corrida multi-turno contra Ollama con muestreo OS (Fase 0.5)."""
    from .ollama_probe import (METRIC_SEMANTICS, OsSampler, http_json,
                               run_turn, sample_os, to_events, unload)

    prompts_data = json.loads(Path(a.prompts).read_text())
    base_prompts = prompts_data["prompts"]
    filler = Path(a.filler).read_text() if a.filler else ""

    observer = Observer()
    run_cfg = RunConfig(
        model_id=a.model, prompts_file=str(a.prompts),
        prompt_hash=sha256_file(a.prompts), seed=42, temperature=0.0,
        do_sample=False, max_tokens=a.num_predict, device="ollama",
        sample_n=1, capture_distributions=False, kv_page_tokens=a.ctx,
    )

    # Tamaño del modelo según el servidor (size_vram vale 0 en macOS/Metal)
    model_size = next((m.get("size") for m in http_json("/api/tags").get("models", [])
                       if m.get("name", "").startswith(a.model)), None)

    with observer.capture(run_cfg) as trace:
        history, turns = [], []
        for t in range(a.turns):
            grow = filler * (a.filler_grow * (t + 1))
            user = base_prompts[t % len(base_prompts)]
            context = (grow + "\n\n".join(history + [user])) if history or grow \
                else user
            sampler = OsSampler(a.sample_interval)
            baseline = sample_os()
            t0 = time.perf_counter()
            metrics, text = run_turn(a.model, context, a.ctx,
                                     a.num_predict, a.keep_alive, sampler=sampler)
            wall = time.perf_counter() - t0
            for e in to_events(trace.run_id, metrics, sampler.samples,
                               observer.now_ns):
                observer.emit(e)
            turns.append({
                "turn": t, "wall_s": round(wall, 3),
                "eval_count": metrics.get("eval_count"),
                "tokps": round(metrics.get("eval_count", 0)
                               / (metrics.get("eval_duration", 1) / 1e9), 2),
                "prompt_eval_count": metrics.get("prompt_eval_count"),
                "prompt_eval_duration": metrics.get("prompt_eval_duration"),
                "load_duration": metrics.get("load_duration"),
                "done_reason": metrics.get("done_reason"),
                "baseline": baseline,
                "samples": len(sampler.samples),
            })
            print(f"turno {t}: tokps={turns[-1]['tokps']} wall={wall:.1f}s "
                  f"prefill_tok={metrics.get('prompt_eval_count')}")
            history.append(f"Q: {user}\nA: {text.strip()}")
        if a.unload_between:
            unload(a.model)

    meta = build_metadata(trace, a.model, "ollama", {})
    meta.update({
        "backend": "ollama", "hooks": "n/a",
        "model_size_bytes": model_size,
        "num_ctx": a.ctx, "num_predict": a.num_predict,
        "turns": turns, "keep_alive": a.keep_alive,
        "filler_file": a.filler, "filler_grow": a.filler_grow,
        "metric_semantics": METRIC_SEMANTICS,
    })
    result = write(trace, {}, meta, a.out)
    print(f"run_id={trace.run_id} turnos={len(turns)}")
    print(f"traza: {result.events_path}")
    return 0


def cmd_report_spill(a) -> int:
    """Tabla tamaño×contexto → tok/s, pageins, swap + régimen + bandwidth."""
    from .ollama_probe import OS_METRICS
    rows = []
    for d, m in _metas(a.traces):
        if m.get("backend") != "ollama":
            continue
        events = pd.read_parquet(d / "events.parquet")
        os_evs = events[(events.source == "os_metric")
                        & events.block_id.isin(f"host.{k}" for k in OS_METRICS)]
        agg = {}
        for metric in OS_METRICS:
            s = os_evs[os_evs.block_id == f"host.{metric}"].sort_values("timestamp_ns")
            if len(s) >= 2:
                agg[f"d_{metric}"] = int(s.size_bytes.iloc[-1] - s.size_bytes.iloc[0])
                if metric == "rss":
                    agg["rss_peak"] = int(s.size_bytes.max())
        tokps = [t["tokps"] for t in m.get("turns", []) if t.get("tokps")]
        total_eval = sum(t.get("eval_count") or 0 for t in m.get("turns", []))
        d_pageins = agg.get("d_pageins", 0)
        d_swapins = agg.get("d_swapins", 0)
        # Régimen: pageins incluye la carga del modelo (file cache frío), así
        # que el criterio de spill es swap de verdad (swapins = páginas
        # leídas de vuelta desde SSD durante la corrida), no pageins.
        regime = "FIT"
        if d_swapins > 2**30:
            slow_load = (m.get("turns") or [{}])[0].get("load_duration", 0) > 30e9
            regime = "SPILL-WEIGHTS?" if slow_load else "SPILL-KV?"
        # bandwidth efectivo de spill: bytes paginados por token × tok/s
        # (solo meaningful en régimen SPILL)
        bw = None
        if regime.startswith("SPILL") and total_eval and tokps:
            bw = (d_pageins / total_eval) * (sum(tokps) / len(tokps)) / 1e6
        rows.append({
            "model": m["model_id"], "ctx": m.get("num_ctx"),
            "size_gb": round((m.get("model_size_bytes") or 0) / 2**30, 2),
            "turns": len(m.get("turns", [])),
            "tokps_mean": round(sum(tokps) / len(tokps), 2) if tokps else None,
            "d_pageins_mb": round(d_pageins / 2**20, 1),
            "d_swapused_mb": round(agg.get("d_swapused", 0) / 2**20, 1),
            "rss_peak_gb": round(agg.get("rss_peak", 0) / 2**30, 2),
            "spill_bw_mbs": round(bw, 1) if bw else None,
            "regime": regime,
        })
    if not rows:
        print("sin trazas ollama en", a.traces)
        return 1
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    if a.csv:
        df.to_csv(a.csv, index=False)
        print(f"csv: {a.csv}")
    return 0


def cmd_report_early_exit(a) -> int:
    """Agregación E1-A: acuerdo medio por capa sobre N corridas con probe.

    La curva de acuerdo ES la CDF de estabilización: agreement[i] = fracción
    de tokens cuyo argmax en capa i ya es el final. Responde la pregunta
    distributiva: ¿qué fracción de tokens estabiliza antes de la última capa?
    """
    curves, first_layers, n_tok = [], {}, 0
    for d, m in _metas(a.traces, a.model):
        ee = m.get("early_exit") or {}
        curve = ee.get("agreement_per_layer")
        if not curve:
            continue
        curves.append((m.get("prompt_index"), curve, ee))
        n_tok += ee.get("n_tokens", 0)
    if not curves:
        print("sin corridas con early_exit en", a.traces)
        return 1
    n_layers = len(curves[0][1])
    mean_curve = [sum(c[1][i] for c in curves) / len(curves)
                  for i in range(n_layers)]
    print(f"corridas={len(curves)} tokens={n_tok} modelo={a.model or 'todos'}")
    for i, a_mean in enumerate(mean_curve):
        print(f"capa {i:2d}: acuerdo medio={a_mean:.3f}")
    # primera capa con acuerdo ≥ τ para cada corrida (perfil por prompt)
    for tau in (0.90, 0.95, 0.99):
        ks = [next((i for i, v in enumerate(c[1]) if v >= tau), None)
              for c in curves]
        hit = sum(1 for k in ks if k is not None and k < n_layers - 1)
        print(f"τ={tau}: ahorro>0 en {hit}/{len(curves)} corridas "
              f"(primera capa media={sum(k for k in ks if k is not None) / len(ks):.1f})")
    if a.csv:
        rows = [{"layer": i, "agreement_mean": round(v, 4)}
                for i, v in enumerate(mean_curve)]
        pd.DataFrame(rows).to_csv(a.csv, index=False)
        print(f"csv: {a.csv}")
    return 0


def cmd_report_moe(a) -> int:
    """Fase 0.6 (ADR-0063): working set de expertos en un MoE real.

    Tres números que el Simulator de Fase 1 necesita: (1) concentración —
    cuántos bloques (capa,experto) cubren 90% de los accesos; (2) expertos
    únicos tocados por token; (3) bytes activos por token vs total de expertos
    (la fracción de pesos que una política expert-aware podría mantener fría).
    """
    import re
    pat = re.compile(r"^L\d+\.expert\.\d+$")
    any_run = False
    for d, m in _metas(a.traces):
        n_exp = (m.get("registry") or {}).get("n_experts", 0)
        if not n_exp:
            continue
        any_run = True
        df = pd.read_parquet(d / "events.parquet")
        ex = df[(df.event_type == "EXECUTE") & df.block_id.str.match(pat)]
        blocks = ex.block_id.value_counts()
        cum = blocks.sort_values(ascending=False).cumsum() / max(len(ex), 1)
        n90 = int((cum < 0.90).sum()) + 1
        n_layers = (m.get("registry") or {}).get("n_layers", 0)
        dec = ex[ex.phase == "decode"]
        per_tok = dec.groupby("token_pos").block_id.nunique()
        expert_mb = float(ex.size_bytes.iloc[0]) / 2**20
        total_expert_mb = n_layers * n_exp * expert_mb
        # bytes activos por token: TODOS los bloques EXECUTE únicos del token
        all_dec = df[(df.event_type == "EXECUTE") & (df.phase == "decode")]
        uniq_sizes = all_dec.drop_duplicates("block_id").set_index("block_id").size_bytes
        active = all_dec.drop_duplicates(["token_pos", "block_id"])
        active_mb = active.groupby("token_pos").size_bytes.sum() / 2**20
        print(f"run={d.name} model={m['model_id']}")
        print(f"  expert EXECUTE={len(ex)} | bloques únicos tocados={len(blocks)} "
              f"de {n_layers * n_exp}")
        print(f"  concentración: {n90} bloques cubren 90% de los accesos "
              f"(reuso medio: {len(ex) / max(len(blocks), 1):.1f}× por bloque)")
        if len(per_tok):
            print(f"  expertos únicos/token (decode): mean={per_tok.mean():.1f} "
                  f"min={per_tok.min()} max={per_tok.max()}")
            print(f"  bytes activos/token (decode): mean={active_mb.mean():.1f} MiB | "
                  f"total expertos: {total_expert_mb:.0f} MiB | "
                  f"fracción expertos tocada: "
                  f"{per_tok.mean() * expert_mb / total_expert_mb:.1%}")
    if not any_run:
        print("sin corridas MoE en", a.traces)
        return 1
    return 0


def cmd_engine_run(a) -> int:
    """Fase 1 (ADR-0069): inferencia real sobre mmap con presión de RAM."""
    from .engine import run_engine
    prompt = a.prompt
    if prompt is None:
        prompts_data = json.loads(Path("prompts/validation.json").read_text())
        prompt = prompts_data["prompts"][0]
    report = run_engine(a.model, a.budget_gb, a.policy, a.max_tokens, prompt,
                        a.out, slack_gb=a.slack_gb, calibrate=a.calibrate,
                        temperature=a.temperature)
    print(json.dumps(report, indent=2))
    return 0


def cmd_simulate(a) -> int:
    """Fase 0.7 (ADR-0065) + Fase 1 preflight (ADR-0066): replay de trazas
    bajo políticas de residencia.

    hlwss (pin estático + SLRU) y hlwss-adaptive (régimen FIT + shrink-to-fit)
    son candidatas; lru y oracle son piso y techo.
    """
    from .simulate import POLICIES, load_run, scale_blocks, simulate
    budgets = [float(x) for x in a.ram_budget_gb.split(",")]
    factors = {}
    for spec in a.scale_component or []:   # ADR-0068 (G2): COMP:factor
        comp, _, f = spec.partition(":")
        factors[comp.strip().upper()] = float(f)
    scenario = "+".join(a.scale_component) if factors else "bf16"
    runs = []
    for d, m in _metas(a.traces):
        if a.run and not d.name.startswith(a.run):
            continue
        if not a.run and not (m.get("registry") or {}).get("n_experts"):
            continue  # default: corridas MoE (las que tienen expertos)
        runs.append((d.name, m))
    if not runs:
        print("sin corridas elegibles en", a.traces)
        return 1
    policies = POLICIES if a.policy == "all" else (a.policy,)
    rows = []
    for run_id, m in runs:
        stream, blocks, kv_bytes, _ = load_run(a.traces, run_id)
        if factors:
            blocks = scale_blocks((m.get("registry") or {}).get("blocks") or {},
                                  factors)
        n_tok = m.get("generated_tokens") or 0
        for bg in budgets:
            budget = int(bg * 2**30)
            per = {}
            for pol in policies:
                r = simulate(stream, blocks, kv_bytes, pol, budget)
                r["run"] = run_id[:8]
                r["model"] = m.get("model_id")
                r["budget_gb"] = bg
                r["scenario"] = scenario
                r["bytes_transferred_gb"] = round(r["bytes_transferred"] / 2**30, 3)
                r["bytes_per_token_mb"] = (round(r["bytes_transferred"] / 2**20 / n_tok, 1)
                                           if n_tok else None)
                r["ram_hit_%"] = round(100 * r["ram_hit_fraction"], 1)
                r["pinned_gb"] = round(r["pinned_bytes"] / 2**30, 2)
                per[pol] = r
                rows.append(r)
            if "lru" in per:
                base = per["lru"]["bytes_transferred"]
                for pol, r in per.items():
                    r["reduction_vs_lru_%"] = (round(100 * (1 - r["bytes_transferred"] / base), 1)
                                               if base else 0.0)
    cols = ["run", "model", "scenario", "policy", "budget_gb",
            "bytes_transferred_gb",
            "bytes_per_token_mb", "ram_hit_%", "n_evictions", "pinned_gb",
            "infeasible", "regime", "coverage", "reduction_vs_lru_%",
            "n_unknown"]
    df = pd.DataFrame(rows)[cols]
    print(df.to_string(index=False))
    if a.csv:
        df.to_csv(a.csv, index=False)
        print(f"csv: {a.csv}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hlwss")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--model", default="Qwen/Qwen3-0.6B")
    r.add_argument("--prompts", default="prompts/validation.json")
    r.add_argument("--out", default="traces")
    r.add_argument("--prompt-idx", type=int, default=0)
    r.add_argument("--max-tokens", type=int, default=256)
    r.add_argument("--sample-n", type=int, default=1)
    r.add_argument("--kv-page-tokens", type=int, default=16)
    r.add_argument("--dtype-bytes", type=float, default=None,
                   help="bytes efectivos por peso (override; p.ej. 0.5 para int4)")
    r.add_argument("--expert-dtype-bytes", type=float, default=None,
                   help="bytes por peso de EXPERTOS con layout mixto (ADR-0063)")
    r.add_argument("--int4-experts", action="store_true",
                   help="MoE con expertos fusionados → int4 propio streaming")
    r.add_argument("--device", default=None)
    r.add_argument("--capture-distributions", action="store_true")
    r.add_argument("--early-exit-probe", action="store_true",
                   help="E1-A: logit-lens por capa (reporte en metadata.early_exit)")
    r.add_argument("--synthetic-moe", action="store_true")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("check")
    c.add_argument("--traces", default="traces")
    c.add_argument("--v", default="all", choices=["all", "V1", "V3", "V4"])
    c.add_argument("--v3-pair", default=None, help="dir_on,dir_off (metadata.json con tokps)")
    c.set_defaults(fn=cmd_check)

    an = sub.add_parser("analyze")
    an.add_argument("--traces", default="traces")
    an.set_defaults(fn=cmd_analyze)

    pr = sub.add_parser("probe", help="Fase 0.5: observación externa de Ollama")
    pr_sub = pr.add_subparsers(dest="probe_cmd", required=True)
    po = pr_sub.add_parser("ollama")
    po.add_argument("--model", required=True)
    po.add_argument("--prompts", default="prompts/validation.json")
    po.add_argument("--out", default="traces")
    po.add_argument("--ctx", type=int, default=4096)
    po.add_argument("--turns", type=int, default=8)
    po.add_argument("--num-predict", type=int, default=256)
    po.add_argument("--keep-alive", default="30m")
    po.add_argument("--sample-interval", type=float, default=0.25)
    po.add_argument("--filler", default="prompts/context-filler.txt")
    po.add_argument("--filler-grow", type=int, default=1,
                    help="repeticiones del filler por turno (crece el contexto)")
    po.add_argument("--unload-between", action="store_true")
    po.set_defaults(fn=cmd_probe_ollama)

    rs = sub.add_parser("report", help="reportes por backend")
    rs_sub = rs.add_subparsers(dest="report_cmd", required=True)
    rsp = rs_sub.add_parser("spill")
    rsp.add_argument("--traces", default="traces")
    rsp.add_argument("--csv", default=None)
    rsp.set_defaults(fn=cmd_report_spill)

    rse = rs_sub.add_parser("early-exit",
                            help="E1-A: agregación de acuerdo por capa")
    rse.add_argument("--traces", default="traces")
    rse.add_argument("--model", default=None)
    rse.add_argument("--csv", default=None)
    rse.set_defaults(fn=cmd_report_early_exit)

    rsm = rs_sub.add_parser("moe",
                            help="Fase 0.6: working set de expertos en MoE real")
    rsm.add_argument("--traces", default="traces")
    rsm.set_defaults(fn=cmd_report_moe)

    sm = sub.add_parser("simulate", help="Fase 0.7/1: replay bajo políticas "
                                          "de residencia (ADR-0065/0066)")
    sm.add_argument("--traces", default="traces")
    sm.add_argument("--run", default=None, help="run_id exacto (default: MoE)")
    sm.add_argument("--policy", default="all",
                    choices=["all", "lru", "hlwss", "hlwss-adaptive", "oracle"])
    sm.add_argument("--ram-budget-gb", default="8", help="lista comma p.ej. 6,8,10")
    sm.add_argument("--scale-component", action="append", default=None,
                    help="G2 (ADR-0068): COMP:factor repetible; 'weights' = "
                         "todos los bloques no derivados. p.ej. PLE:0.25")
    sm.add_argument("--csv", default=None)
    sm.set_defaults(fn=cmd_simulate)

    en = sub.add_parser("engine", help="Fase 1 (ADR-0069): engine real "
                        "mmap + presión + pageins")
    en_sub = en.add_subparsers(dest="engine_cmd", required=True)
    enr = en_sub.add_parser("run")
    enr.add_argument("--model", required=True)
    enr.add_argument("--budget-gb", type=float, required=True)
    enr.add_argument("--policy", default="os-lru",
                     choices=["os-lru", "hlwss-pin", "hlwss-evict"])
    enr.add_argument("--max-tokens", type=int, default=64)
    enr.add_argument("--prompt", default=None,
                     help="texto libre; default prompts/validation.json p0")
    enr.add_argument("--slack-gb", type=float, default=0.0)
    enr.add_argument("--temperature", type=float, default=0.0,
                     help="> 0: muestreo multinomial con temperatura; 0 = greedy")
    enr.add_argument("--calibrate", action="store_true",
                     help="corrida corta para medir overhead/slack")
    enr.add_argument("--out", default="traces-engine")
    enr.set_defaults(fn=cmd_engine_run)

    a = p.parse_args(argv)
    return a.fn(a)

if __name__ == "__main__":
    sys.exit(main())
