"""Tests de ollama_probe — sin red: vm_stat mockeado, to_events, sampler."""

from types import SimpleNamespace

import pandas as pd

from hlwss.events import Event
from hlwss.ollama_probe import (OS_METRICS, OsSampler, parse_vm_stat,
                                sample_os, to_events)

VM_STAT_SAMPLE = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              12345.
Pages active:                           253777.
Pageins:                                52377749.
Pageouts:                                907975.
Swapins:                                13620041.
Swapouts:                               16318903.
"""


def test_parse_vm_stat_bytes():
    vm = parse_vm_stat(VM_STAT_SAMPLE)
    assert vm["pageins"] == 52377749 * 16384
    assert vm["swapins"] == 13620041 * 16384
    assert vm["pageouts"] == 907975 * 16384


def test_parse_vm_stat_respects_page_size_line():
    vm = parse_vm_stat("Mach (page size of 4096 bytes)\nPageins: 100.\n")
    assert vm["pageins"] == 100 * 4096


def test_parse_vm_stat_linux_returns_empty():
    assert parse_vm_stat("some linux output without counters") == {}


def test_sample_os_shape(monkeypatch):
    monkeypatch.setattr("hlwss.ollama_probe.vm_stat",
                        lambda: {"pageins": 100, "swapins": 5,
                                 "swapouts": 7, "pageouts": 0})
    monkeypatch.setattr("hlwss.ollama_probe.swapused_bytes", lambda: 2048)
    monkeypatch.setattr("hlwss.ollama_probe._rss_bytes", lambda pid: 4096)
    snap = sample_os(pid=123)
    assert snap["rss"] == 4096 and snap["pageins"] == 100
    assert snap["swapused"] == 2048
    assert set(snap) == set(OS_METRICS)


def test_os_sampler_collects_and_stops():
    sampler = OsSampler(interval_s=0.05)
    sampler.start(pid=None)
    import time
    time.sleep(0.2)
    sampler.stop()
    assert len(sampler.samples) >= 2
    ts0 = sampler.samples[0][0]
    assert all(isinstance(s, dict) for _, s in sampler.samples)
    assert all(ts >= ts0 for ts, _ in sampler.samples)


def test_to_events_request_phase_and_sources():
    metrics = {"eval_count": 42, "eval_duration": 2_000_000_000,
               "done_reason": "stop"}
    samples = [(1000, {k: v for k, v in zip(OS_METRICS, range(5))}),
               (2000, {k: v for k, v in zip(OS_METRICS, range(5, 10))})]
    clock = iter([5000])
    events = to_events("run-1", metrics, samples, lambda: next(clock))
    server = [e for e in events if e.source == "server_metric"]
    os_evs = [e for e in events if e.source == "os_metric"]
    assert len(server) == 1 and len(os_evs) == 2 * len(OS_METRICS)
    for e in events:
        assert e.phase == "request" and e.token_pos is None
    assert server[0].size_bytes == 42
    assert server[0].duration_ns == 2_000_000_000
    rss = [e for e in os_evs if e.block_id == "host.rss"]
    assert {e.size_bytes for e in rss} == {0, 5}  # primera métrica de cada muestra
    # validación del schema por construcción (Event.__post_init__ no lanzó)


def test_to_events_skips_none_metrics():
    samples = [(1000, {"rss": None, "pageins": 7, "swapins": None,
                       "swapouts": None, "swapused": None})]
    events = to_events("r", {}, samples, lambda: 0)
    os_evs = [e for e in events if e.source == "os_metric"]
    assert len(os_evs) == 1 and os_evs[0].block_id == "host.pageins"


def test_spill_report_on_synthetic_trace(tmp_path):
    """report spill lee metadata backend=ollama y agrega métricas OS."""
    import json
    from hlwss.cli import cmd_report_spill
    d = tmp_path / "runx"
    d.mkdir()
    rows = []
    ts = 0
    for i, v in enumerate([100, 200, 300]):  # pageins creciente
        ts += 1
        rows.append({"timestamp_ns": ts, "token_pos": None, "phase": "request",
                     "layer": None, "component": "OTHER",
                     "block_id": "host.pageins", "event_type": "LOGICAL_ACCESS",
                     "size_bytes": v * 2**20, "duration_ns": None,
                     "source": "os_metric"})
    rows.append({"timestamp_ns": 4, "token_pos": None, "phase": "request",
                 "layer": None, "component": "OTHER", "block_id": None,
                 "event_type": "EXECUTE", "size_bytes": 10,
                 "duration_ns": 1_000_000_000, "source": "server_metric"})
    pd.DataFrame(rows).to_parquet(d / "events.parquet")
    meta = {"backend": "ollama", "model_id": "m", "num_ctx": 4096,
            "model_size_bytes": 2**33, "turns": [{"tokps": 20.0, "eval_count": 10}]}
    (d / "metadata.json").write_text(json.dumps(meta))
    a = SimpleNamespace(traces=str(tmp_path), csv=None)
    assert cmd_report_spill(a) == 0
