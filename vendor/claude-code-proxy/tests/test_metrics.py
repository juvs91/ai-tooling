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
        m.record(_make_log(intent="BUILD"))
        m.record(_make_log(intent="CHAT"))
        stats = m.get_stats()
        assert stats["intents"]["CHAT"] == 2
        assert stats["intents"]["BUILD"] == 1

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


class TestToolQualityCounters:
    """Increment 1: tool call quality counters."""

    def test_increment_tool_counter_native(self):
        m = ProxyMetrics()
        m.increment_tool_counter("native")
        m.increment_tool_counter("native")
        assert m.tool_calls_native == 2

    def test_increment_tool_counter_xml_extracted(self):
        m = ProxyMetrics()
        m.increment_tool_counter("xml_extracted")
        assert m.tool_calls_xml_extracted == 1

    def test_increment_tool_counter_recovered(self):
        m = ProxyMetrics()
        m.increment_tool_counter("recovered")
        assert m.tool_calls_recovered == 1

    def test_increment_tool_counter_truncated(self):
        m = ProxyMetrics()
        m.increment_tool_counter("truncated")
        assert m.tool_calls_truncated == 1

    def test_increment_tool_counter_hallucinated(self):
        m = ProxyMetrics()
        m.increment_tool_counter("hallucinated")
        assert m.tool_calls_hallucinated == 1

    def test_tool_quality_in_stats(self):
        m = ProxyMetrics()
        m.increment_tool_counter("native")
        m.increment_tool_counter("native")
        m.increment_tool_counter("xml_extracted")
        m.increment_tool_counter("recovered")
        m.increment_tool_counter("truncated")
        stats = m.get_stats()
        tq = stats["tool_quality"]
        assert tq["native"] == 2
        assert tq["xml_extracted"] == 1
        assert tq["recovered"] == 1
        assert tq["truncated"] == 1
        assert tq["hallucinated"] == 0
        assert tq["total"] == 5
        # success = native(2) + xml(1) + recovered(1) = 4 / 5 = 80%
        assert tq["success_rate_pct"] == 80.0

    def test_tool_quality_100_pct_when_all_succeed(self):
        m = ProxyMetrics()
        m.increment_tool_counter("native")
        m.increment_tool_counter("xml_extracted")
        stats = m.get_stats()
        assert stats["tool_quality"]["success_rate_pct"] == 100.0

    def test_tool_quality_0_pct_when_all_fail(self):
        m = ProxyMetrics()
        m.increment_tool_counter("truncated")
        m.increment_tool_counter("hallucinated")
        stats = m.get_stats()
        assert stats["tool_quality"]["success_rate_pct"] == 0.0

    def test_tool_quality_empty(self):
        m = ProxyMetrics()
        stats = m.get_stats()
        tq = stats["tool_quality"]
        assert tq["total"] == 0
        assert tq["success_rate_pct"] == 0.0

    def test_concurrent_tool_counter_increments(self):
        m = ProxyMetrics()
        errors = []

        def inc(counter, n):
            try:
                for _ in range(200):
                    m.increment_tool_counter(counter)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=inc, args=("native", 0)),
            threading.Thread(target=inc, args=("native", 1)),
            threading.Thread(target=inc, args=("xml_extracted", 0)),
            threading.Thread(target=inc, args=("truncated", 0)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert m.tool_calls_native == 400
        assert m.tool_calls_xml_extracted == 200
        assert m.tool_calls_truncated == 200


class TestPerModelQualityTracking:
    """Increment 2: per-model event tracking."""

    def test_record_model_event_basic(self):
        m = ProxyMetrics()
        m.record_model_event("openai/glm-4.7", "tool_success")
        m.record_model_event("openai/glm-4.7", "tool_success")
        m.record_model_event("openai/glm-4.7", "tool_failure")
        stats = m.get_stats()
        mq = stats["model_quality"]
        assert mq["glm-4.7"]["tool_success"] == 2
        assert mq["glm-4.7"]["tool_failure"] == 1

    def test_record_model_event_strips_provider_prefix(self):
        m = ProxyMetrics()
        m.record_model_event("openai/deepseek-chat", "recovery")
        m.record_model_event("anthropic/glm-4.7", "tool_success")
        stats = m.get_stats()
        mq = stats["model_quality"]
        assert "deepseek-chat" in mq
        assert "glm-4.7" in mq
        # Prefixes should NOT appear as keys
        assert "openai/deepseek-chat" not in mq

    def test_record_model_event_no_prefix(self):
        m = ProxyMetrics()
        m.record_model_event("local-model", "tool_success")
        stats = m.get_stats()
        assert "local-model" in stats["model_quality"]

    def test_record_model_event_multiple_models(self):
        m = ProxyMetrics()
        m.record_model_event("openai/glm-4.7", "tool_success")
        m.record_model_event("openai/deepseek-chat", "tool_success")
        m.record_model_event("openai/deepseek-chat", "truncation")
        stats = m.get_stats()
        mq = stats["model_quality"]
        assert mq["glm-4.7"] == {"tool_success": 1}
        assert mq["deepseek-chat"] == {"tool_success": 1, "truncation": 1}

    def test_model_quality_empty(self):
        m = ProxyMetrics()
        stats = m.get_stats()
        assert stats["model_quality"] == {}

    def test_concurrent_model_events(self):
        m = ProxyMetrics()
        errors = []

        def record(model):
            try:
                for _ in range(100):
                    m.record_model_event(model, "tool_success")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=record, args=(f"openai/model-{i}",))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        mq = m.get_stats()["model_quality"]
        for i in range(4):
            assert mq[f"model-{i}"]["tool_success"] == 100


class TestClassifierDisagreementTracking:
    """Increment 3: classifier accuracy validation."""

    def test_increment_classifier_disagreement(self):
        m = ProxyMetrics()
        m.increment_classifier_disagreement()
        m.increment_classifier_disagreement()
        assert m.classifier_disagreements == 2

    def test_classifier_stats_with_disagreements(self):
        m = ProxyMetrics()
        m.classifier_llm_success = 10
        m.increment_classifier_disagreement()
        m.increment_classifier_disagreement()
        stats = m.get_stats()
        cls = stats["classifier"]
        assert cls["disagreements"] == 2
        # agreement = (1 - 2/10) * 100 = 80%
        assert cls["agreement_rate_pct"] == 80.0

    def test_classifier_stats_no_disagreements(self):
        m = ProxyMetrics()
        m.classifier_llm_success = 5
        stats = m.get_stats()
        cls = stats["classifier"]
        assert cls["disagreements"] == 0
        assert cls["agreement_rate_pct"] == 100.0

    def test_classifier_stats_zero_llm_calls(self):
        m = ProxyMetrics()
        stats = m.get_stats()
        cls = stats["classifier"]
        assert cls["agreement_rate_pct"] == 100.0

    def test_concurrent_classifier_disagreements(self):
        m = ProxyMetrics()
        errors = []

        def inc():
            try:
                for _ in range(100):
                    m.increment_classifier_disagreement()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=inc) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert m.classifier_disagreements == 400


class TestCostTracking:
    """Cost tracking per request, per model, per intent."""

    def test_cost_recorded_in_stats(self):
        m = ProxyMetrics()
        m.record(_make_log(model_used="openai/glm-4.7", intent="PLAN", cost_usd=0.015))
        m.record(_make_log(model_used="deepseek-chat", intent="CHAT", cost_usd=0.001))
        stats = m.get_stats()
        assert stats["cost"]["total_usd"] == 0.016
        assert stats["cost"]["by_model"]["glm-4.7"] == 0.015
        assert stats["cost"]["by_model"]["deepseek-chat"] == 0.001
        assert stats["cost"]["by_intent"]["PLAN"] == 0.015
        assert stats["cost"]["by_intent"]["CHAT"] == 0.001

    def test_cost_avg_per_request(self):
        m = ProxyMetrics()
        m.record(_make_log(cost_usd=0.01))
        m.record(_make_log(cost_usd=0.03))
        stats = m.get_stats()
        assert stats["cost"]["avg_per_request"] == 0.02

    def test_cost_zero_when_no_cost(self):
        m = ProxyMetrics()
        m.record(_make_log())  # default cost_usd=0.0
        stats = m.get_stats()
        assert stats["cost"]["total_usd"] == 0.0
        assert stats["cost"]["by_model"] == {}
        assert stats["cost"]["by_intent"] == {}

    def test_cost_strips_provider_prefix(self):
        m = ProxyMetrics()
        m.record(_make_log(model_used="openai/MiniMax-M2.5", cost_usd=0.005))
        stats = m.get_stats()
        assert "MiniMax-M2.5" in stats["cost"]["by_model"]
        assert "openai/MiniMax-M2.5" not in stats["cost"]["by_model"]

    def test_cost_accumulates_same_model(self):
        m = ProxyMetrics()
        m.record(_make_log(model_used="glm-4.7", cost_usd=0.01))
        m.record(_make_log(model_used="glm-4.7", cost_usd=0.02))
        stats = m.get_stats()
        assert stats["cost"]["by_model"]["glm-4.7"] == 0.03


class TestUpdateStreamingLog:
    """Tests for update_streaming_log (post-stream metrics update)."""

    def test_updates_output_tokens(self):
        m = ProxyMetrics()
        m.record(_make_log(is_stream=True, output_tokens=0))
        m.update_streaming_log(output_tokens=1500)
        assert m.total_output_tokens == 1500
        assert m._logs[-1].output_tokens == 1500

    def test_updates_quality_score(self):
        m = ProxyMetrics()
        m.record(_make_log(is_stream=True, is_analysis=True))
        assert m._logs[-1].quality_score == 1.0  # default
        # record() skips streaming for quality tracking (output_tokens=0 at record time)
        assert m.analysis_quality_sum == 0.0
        assert m.analysis_quality_count == 0
        m.update_streaming_log(quality_score=0.65)
        assert m._logs[-1].quality_score == 0.65
        # update_streaming_log adds quality directly (not as delta)
        assert m.analysis_quality_sum == 0.65
        assert m.analysis_quality_count == 1

    def test_updates_cost(self):
        m = ProxyMetrics()
        m.record(_make_log(is_stream=True, model_used="glm-4.7", intent="PLAN"))
        m.update_streaming_log(cost_usd=0.005)
        assert m.total_cost_usd == 0.005
        assert m.cost_by_model["glm-4.7"] == 0.005
        assert m.cost_by_intent["PLAN"] == 0.005

    def test_skips_non_stream_log(self):
        m = ProxyMetrics()
        m.record(_make_log(is_stream=False, output_tokens=50))
        m.update_streaming_log(output_tokens=9999, quality_score=0.1)
        # Should NOT update — last log is non-streaming
        assert m.total_output_tokens == 50
        assert m._logs[-1].output_tokens == 50

    def test_skips_empty_logs(self):
        m = ProxyMetrics()
        # Should not crash on empty deque
        m.update_streaming_log(output_tokens=100)
        assert m.total_output_tokens == 0

    def test_analysis_quality_streaming_accounting(self):
        """Streaming analysis: record() defers quality to update_streaming_log()."""
        m = ProxyMetrics()
        m.record(_make_log(is_stream=True, is_analysis=True, output_tokens=0))
        # record() skips quality for streaming (output_tokens=0)
        assert m.analysis_quality_sum == 0.0
        assert m.analysis_quality_count == 0
        m.update_streaming_log(quality_score=0.4)
        # update_streaming_log adds quality directly
        assert m.analysis_quality_sum == 0.4
        assert m.analysis_quality_count == 1
        stats = m.get_stats()
        assert stats["analysis_avg_quality"] == 0.4

    def test_analysis_quality_non_streaming(self):
        """Non-streaming analysis: record() handles quality directly."""
        m = ProxyMetrics()
        m.record(_make_log(is_stream=False, is_analysis=True, output_tokens=500, quality_score=0.8))
        assert m.analysis_quality_sum == 0.8
        assert m.analysis_quality_count == 1
        stats = m.get_stats()
        assert stats["analysis_avg_quality"] == 0.8

    def test_analysis_quality_multiple_streaming(self):
        """Multiple streaming analysis requests produce correct average."""
        m = ProxyMetrics()
        # Request 1: quality 0.6
        m.record(_make_log(is_stream=True, is_analysis=True, output_tokens=0, phase="PLAN"))
        m.update_streaming_log(quality_score=0.6)
        # Request 2: quality 0.8
        m.record(_make_log(is_stream=True, is_analysis=True, output_tokens=0, phase="PLAN"))
        m.update_streaming_log(quality_score=0.8)
        stats = m.get_stats()
        # avg = (0.6 + 0.8) / 2 = 0.7
        assert stats["analysis_avg_quality"] == 0.7
        assert m.analysis_quality_count == 2

    def test_phase_quality_updated_by_streaming_log(self):
        """update_streaming_log must update phase quality tracking."""
        m = ProxyMetrics()
        m.record(_make_log(is_stream=True, phase="PLAN", output_tokens=0))
        m.update_streaming_log(quality_score=0.75)
        stats = m.get_stats()
        plan_quality = stats["quality_by_phase"]["PLAN"]
        assert plan_quality["avg_quality"] == 0.75
        assert plan_quality["count"] == 1

    def test_phase_quality_non_streaming(self):
        """Non-streaming phase quality is tracked in record()."""
        m = ProxyMetrics()
        m.record(_make_log(is_stream=False, phase="EXECUTE", output_tokens=200, quality_score=0.9))
        stats = m.get_stats()
        exec_quality = stats["quality_by_phase"]["EXECUTE"]
        assert exec_quality["avg_quality"] == 0.9

    def test_analysis_quality_never_negative(self):
        """The old bug produced negative quality. Verify it's fixed."""
        m = ProxyMetrics()
        m.record(_make_log(is_stream=True, is_analysis=True, output_tokens=0))
        m.update_streaming_log(quality_score=0.3)
        stats = m.get_stats()
        assert stats["analysis_avg_quality"] >= 0.0


class TestClassifierValidationEvents:
    """Tests for classifier post-response validation via record_model_event."""

    def test_validated_wrong_chat(self):
        m = ProxyMetrics()
        m.record_model_event("classifier", "validated_wrong_chat")
        stats = m.get_stats()
        assert stats["model_quality"]["classifier"]["validated_wrong_chat"] == 1

    def test_validated_wrong_building(self):
        m = ProxyMetrics()
        m.record_model_event("classifier", "validated_wrong_building")
        m.record_model_event("classifier", "validated_wrong_building")
        stats = m.get_stats()
        assert stats["model_quality"]["classifier"]["validated_wrong_building"] == 2

    def test_both_validations_tracked_separately(self):
        m = ProxyMetrics()
        m.record_model_event("classifier", "validated_wrong_chat")
        m.record_model_event("classifier", "validated_wrong_building")
        stats = m.get_stats()
        cls = stats["model_quality"]["classifier"]
        assert cls["validated_wrong_chat"] == 1
        assert cls["validated_wrong_building"] == 1


class TestGetStatsFullShape:
    """Verify the complete stats dict shape includes all new fields."""

    def test_stats_has_all_new_sections(self):
        m = ProxyMetrics()
        stats = m.get_stats()
        # Increment 1
        assert "tool_quality" in stats
        assert set(stats["tool_quality"].keys()) == {
            "native", "xml_extracted", "recovered",
            "truncated", "hallucinated", "total", "success_rate_pct",
        }
        # Increment 2
        assert "model_quality" in stats
        # Increment 3
        assert "disagreements" in stats["classifier"]
        assert "agreement_rate_pct" in stats["classifier"]
        # Cost tracking
        assert "cost" in stats
        assert set(stats["cost"].keys()) == {
            "total_usd", "by_model", "by_intent", "avg_per_request",
        }
