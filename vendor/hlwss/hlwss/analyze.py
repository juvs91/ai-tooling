"""Analyzer — DuckDB embebido sobre los Parquet (sin servidor, sin NoSQL).

Reproduce los números teóricos de V1 por construcción; da las tres vistas
canónicas: bytes/token, working set por paso, reuso por bloque.
"""

from pathlib import Path

import duckdb

BYTES_PER_TOKEN = """SELECT phase, token_pos, SUM(size_bytes)/1e6 AS mb
FROM events GROUP BY phase, token_pos ORDER BY token_pos"""

WORKING_SET = """SELECT token_pos, COUNT(DISTINCT block_id) AS blocks,
    SUM(size_bytes) FILTER (WHERE first_seen) / 1e6 AS new_mb
FROM (SELECT *, MIN(timestamp_ns) OVER (PARTITION BY block_id) = timestamp_ns AS first_seen
      FROM events) GROUP BY token_pos ORDER BY token_pos"""

REUSE = """SELECT block_id, COUNT(*) AS touches, SUM(size_bytes)/1e9 AS total_gb
FROM events WHERE event_type='LOGICAL_ACCESS'
GROUP BY block_id ORDER BY total_gb DESC"""


def _con(events_path: str | Path):
    rel = Path(events_path).resolve().as_posix()
    return duckdb.connect().execute(f"CREATE VIEW events AS SELECT * FROM read_parquet('{rel}')")


def report(traces_dir: str | Path, run_id: str) -> str:
    con = _con(Path(traces_dir) / run_id / "events.parquet")
    bpt = con.execute(BYTES_PER_TOKEN).fetchdf()
    ws = con.execute(WORKING_SET).fetchdf()
    reuse = con.execute(REUSE).fetchdf()
    lines = [f"== analyze run {run_id} ==",
             f"decode steps: {len(bpt[bpt.phase == 'decode'])}",
             f"bytes/token decode (MB): mean={bpt[bpt.phase=='decode'].mb.mean():.1f} "
             f"min={bpt[bpt.phase=='decode'].mb.min():.1f} max={bpt[bpt.phase=='decode'].mb.max():.1f}",
             f"working set: blocks vivo={int(ws.blocks.max())}, "
             f"new_bytes/step mean={ws.new_mb.mean():.2f} MB",
             "top-5 bloques por bytes tocados:",
             reuse.head(5).to_string(index=False)]
    return "\n".join(lines)
