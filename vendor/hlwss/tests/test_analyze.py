"""Test del analyzer DuckDB sobre una traza mínima escrita a Parquet."""

import pandas as pd

from hlwss.analyze import report


def test_report_units_and_views(tmp_path):
    rows = []
    ts = 0
    for t in (None, 1, 2):  # prefill + 2 pasos decode
        phase = "prefill" if t is None else "decode"
        for bid, comp, size in [("embed", "EMBED", 100), ("L0.attn", "ATTN", 400),
                                ("L0.kv", "KV", 50)]:
            for et in ("EXECUTE", "LOGICAL_ACCESS"):
                ts += 1
                rows.append({"timestamp_ns": ts, "token_pos": t, "phase": phase,
                             "layer": 0, "component": comp, "block_id": bid,
                             "event_type": et, "size_bytes": size})
    d = tmp_path / "run1"
    d.mkdir()
    pd.DataFrame(rows).to_parquet(d / "events.parquet")

    out = report(tmp_path, "run1")
    assert "decode steps: 2" in out
    # new_bytes/step: solo primer visto por bloque (embed+attn+kv = 550 B = 0.00055 MB)
    assert "new_bytes/step mean=0.00 MB" in out
    assert "L0.attn" in out
