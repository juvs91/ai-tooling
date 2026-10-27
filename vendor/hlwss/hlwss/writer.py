"""Writer — vuelca Trace a Parquet + sidecar JSON de metadata (PRD §2.5).

Salida por corrida: traces/{run_id}/events.parquet,
distributions.parquet (solo con flag) y metadata.json. Validación R7:
request_id de cada evento == run_id de la metadata.
"""

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .observer import Trace

SCHEMA_VERSION = "0.4"

EVENT_SCHEMA = pa.schema([
    ("timestamp_ns", pa.int64()),
    ("request_id", pa.string()),
    ("token_pos", pa.int64()),
    ("phase", pa.string()),
    ("layer", pa.int64()),
    ("component", pa.string()),
    ("block_id", pa.string()),
    ("event_type", pa.string()),
    ("size_bytes", pa.int64()),
    ("duration_ns", pa.int64()),
    ("source", pa.string()),
])


@dataclass(frozen=True, slots=True)
class WriteResult:
    events_path: Path
    distributions_path: Path | None
    metadata_path: Path


def _ram_total_gb() -> float | None:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 2)
    except (ValueError, OSError, AttributeError):
        return None


def _ram_available_gb() -> float | None:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / 2**30, 2)
    except (ValueError, OSError, AttributeError):
        return None


def _vm_stat() -> dict:
    """Líneas de swap de vm_stat (macOS) — sin deps externas."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        swap_lines = [ln.strip() for ln in out.splitlines() if "wap" in ln]
        used = next((ln for ln in swap_lines if "used" in ln.lower()), None)
        gb = None
        if used:
            digits = "".join(ch for ch in used if ch.isdigit() or ch == ".")
            gb = round(float(digits) / 2**30, 3) if digits else None
        return {"swap_used_at_start_gb": gb, "raw": swap_lines}
    except (OSError, subprocess.SubprocessError):
        return {"swap_used_at_start_gb": None, "raw": []}


def build_metadata(trace: Trace, model_id: str, backend_version: str,
                   registry_sidecar: dict) -> dict:
    cfg = trace.cfg
    meta = {
        "schema_version": SCHEMA_VERSION,
        "run_id": trace.run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": model_id,
        "quant": "unknown",               # lo fija el adapter desde torch_dtype
        "n_layers": registry_sidecar.get("n_layers", 0),
        "n_experts": registry_sidecar.get("n_experts", 0),
        "backend": "pytorch",
        "backend_version": backend_version,
        "hardware": platform.machine(),
        "os": platform.platform(),
        "ram_total_gb": _ram_total_gb(),
        "ram_available_gb": _ram_available_gb(),
        "ssd_model_path": None,           # lo fija el adapter (ruta del snapshot HF)
        "seed": cfg.seed,
        "temperature": cfg.temperature,
        "do_sample": cfg.do_sample,
        "prompt_hash": cfg.prompt_hash,
        "max_tokens": cfg.max_tokens,
        "context_window": registry_sidecar.get("context_window", 0),
        "kv_model": registry_sidecar.get("kv_model_layers", {}),
        "kv_page_tokens": cfg.kv_page_tokens,
        "sample_n": cfg.sample_n,
        "capture_distributions": cfg.capture_distributions,
        "quality_harness": "wikitext2-ppl-slice-v1",
        "clock": "time.monotonic_ns",
    }
    meta.update(_vm_stat())
    return meta


def write(trace: Trace, registry_sidecar: dict, metadata: dict,
          out_dir: str | Path) -> WriteResult:
    for e in trace.events:
        if e.request_id != trace.run_id:
            raise ValueError(f"R7: request_id {e.request_id} != run_id {trace.run_id}")
    run_dir = Path(out_dir) / trace.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    rows = [e.to_row() for e in trace.events]
    events_path = run_dir / "events.parquet"
    table = pa.Table.from_pylist(rows, schema=EVENT_SCHEMA)
    pq.write_table(table, events_path)

    dist_path = None
    if trace.dists:
        dist_path = run_dir / "distributions.parquet"
        dt = pa.table({
            "run_id": [trace.run_id] * len(trace.dists),
            "token_pos": [r[0] for r in trace.dists],
            "layer": [r[1] for r in trace.dists],
            "component": [r[2] for r in trace.dists],
            "metric": [r[3] for r in trace.dists],
            "value": [r[4] for r in trace.dists],
        })
        pq.write_table(dt, dist_path)

    metadata = dict(metadata)
    metadata["registry"] = registry_sidecar
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, default=str))
    return WriteResult(events_path, dist_path, metadata_path)
