# tests/test_metrics.py
"""Tests for utils/metrics.py — ProxyMetrics + RequestLog."""
import threading
from utils.metrics import ProxyMetrics, RequestLog


def _make_log(**overrides) -> RequestLog:
    defaults = dict(
        timestamp="2026-01-01T00:00:00Z",
        intent="CHAT",
        model_requested="claude-sonnet-4-5-20250929",
        model_used="openai/glm-4.7",
        provider="primary",
        input_tokens=100,
        output_tokens=50,
        latency_ms=200,
        is_fallback=False,
        is_stream=False,
        error=None,
    )
    defaults.update(overrides)
    return RequestLog(**defaults)


class TestProxyMetrics:
    def test_record_increments_counters(self):
        m = ProxyMetrics()
        m.record(_make_log())
        assert m.total_requests == 1
        assert m.total_input_tokens == 100
        assert m.total_output_tokens == 50

    def test_record_tracks_errors(self):
        m = ProxyMetrics()
        m.record(_make_log(error="timeout"))
        assert m.total_errors == 1
        assert m.provider_errors["primary"] == 1

    def test_record_tracks_fallbacks(self):
        m = ProxyMetrics()
        m.record(_make_log(provider="fallback_1", is_fallback=True))
        assert m.total_fallbacks == 1
        assert m.provider_counts["fallback_1"] == 1

    def test_get_stats_calculates_fallback_rate(self):
        m = ProxyMetrics()
        m.record(_make_log())
        m.record(_make_log(provider="fallback_1", is_fallback=True))
        stats = m.get_stats()
        assert stats["total_requests"] == 2
        assert stats["total_fallbacks"] == 1
        assert stats["fallback_rate_pct"] == 50.0

    def test_get_stats_avg_latency(self):
        m = ProxyMetrics()
        m.record(_make_log(latency_ms=100))
        m.record(_make_log(latency_ms=300))
        stats = m.get_stats()
        assert stats["providers"]["primary"]["avg_latency_ms"] == 200

    def test_get_stats_intent_counts(self):
        m = ProxyMetrics()
        m.record(_make_log(intent="CHAT"))
        m.record(_make_log(intent="BUILDING"))
        m.record(_make_log(intent="CHAT"))
        stats = m.get_stats()
        assert stats["intents"]["CHAT"] == 2
        assert stats["intents"]["BUILDING"] == 1

    def test_get_recent_returns_last_n(self):
        m = ProxyMetrics()
        for i in range(10):
            m.record(_make_log(latency_ms=i))
        recent = m.get_recent(3)
        assert len(recent) == 3
        assert recent[0]["latency_ms"] == 7
        assert recent[2]["latency_ms"] == 9

    def test_ring_buffer_respects_maxlen(self):
        m = ProxyMetrics(max_logs=5)
        for i in range(10):
            m.record(_make_log(latency_ms=i))
        recent = m.get_recent(100)
        assert len(recent) == 5
        # Only last 5 should remain
        assert recent[0]["latency_ms"] == 5

    def test_empty_stats(self):
        m = ProxyMetrics()
        stats = m.get_stats()
        assert stats["total_requests"] == 0
        assert stats["fallback_rate_pct"] == 0.0
        assert stats["providers"] == {}

    def test_concurrent_records_no_corruption(self):
        m = ProxyMetrics()
        errors = []

        def writer(n):
            try:
                for _ in range(100):
                    m.record(_make_log(provider=f"p{n}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert m.total_requests == 400

    def test_cache_counters_in_stats(self):
        m = ProxyMetrics()
        m.cache_hits = 10
        m.cache_misses = 5
        stats = m.get_stats()
        assert stats["cache"]["hits"] == 10
        assert stats["cache"]["misses"] == 5
