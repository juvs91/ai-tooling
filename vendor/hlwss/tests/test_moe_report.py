"""Tests de cmd_report_moe (Fase 0.6, ADR-0063) — eventos sintéticos."""

import json
import re
from types import SimpleNamespace

import pandas as pd

from hlwss.cli import cmd_report_moe


def _events(rows) -> pd.DataFrame:
    cols = ["timestamp_ns", "run_id", "token_pos", "phase", "layer",
            "component", "block_id", "event_type", "size_bytes",
            "duration_ns", "source"]
    return pd.DataFrame(rows, columns=cols)


def _mk_run(tmp_path, events: pd.DataFrame, n_experts=3, n_layers=2):
    d = tmp_path / "run-moe"
    d.mkdir()
    events.to_parquet(d / "events.parquet")
    (d / "metadata.json").write_text(json.dumps({
        "model_id": "moe-test",
        "registry": {"n_experts": n_experts, "n_layers": n_layers},
    }))
    return d


def _expert_exec(layer, expert, token, size=2**20):
    return [0, "r", token, "decode", layer, "EXPERT",
            f"L{layer}.expert.{expert}", "EXECUTE", size, None, "hook"]


def test_report_moe_numeros_clave(tmp_path, capsys):
    rows = []
    # 2 capas × 3 expertos; tokens 0 y 1 tocan el experto 0 siempre (caliente)
    for t in (0, 1):
        rows.append(_expert_exec(0, 0, t))
        rows.append(_expert_exec(1, 0, t))
    rows.append(_expert_exec(0, 1, 0))
    rows.append(_expert_exec(0, 2, 1))
    _mk_run(tmp_path, _events(rows))
    a = SimpleNamespace(traces=str(tmp_path))
    assert cmd_report_moe(a) == 0
    out = capsys.readouterr().out
    # conteos: L0.e0×2, L1.e0×2, L0.e1×1, L0.e2×1 → cum 33%,67%,83%,100%
    # → hacen falta 4 bloques para cruzar 90% (3 solo llegan a 83%)
    assert "expert EXECUTE=6" in out
    assert "bloques únicos tocados=4 de 6" in out
    assert "concentración: 4 bloques cubren 90%" in out
    # cada token toca experto 0 en ambas capas + 1 exclusivo = 3 únicos
    assert "expertos únicos/token (decode): mean=3.0 min=3 max=3" in out
    assert "fracción expertos tocada: 50.0%" in out


def test_report_moe_ignora_no_moe(tmp_path, capsys):
    d = tmp_path / "run-denso"
    d.mkdir()
    _events([[0, "r", 0, "decode", 0, "FFN", "L0.ffn", "EXECUTE", 8, None, "hook"]]
            ).to_parquet(d / "events.parquet")
    (d / "metadata.json").write_text(json.dumps({
        "model_id": "denso", "registry": {"n_experts": 0, "n_layers": 1}}))
    a = SimpleNamespace(traces=str(tmp_path))
    assert cmd_report_moe(a) == 1  # sin corridas MoE
    assert "sin corridas MoE" in capsys.readouterr().out
