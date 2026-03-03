# utils/metrics.py — Observability for claude-code-proxy
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, asdict
from threading import Lock


@dataclass
class RequestLog:
    timestamp: str
    intent: str
    model_requested: str      # what Claude Code sent
    model_used: str           # what was actually used
    provider: str             # "primary", "fallback_1", etc.
    input_tokens: int
    output_tokens: int
    latency_ms: int
    is_fallback: bool
    is_stream: bool
    phase: str = "EXECUTE"
    is_analysis: bool = False
    refinement_attempts: int = 0
    quality_score: float = 1.0
    cost_usd: float = 0.0
    error: str | None = None


class ProxyMetrics:
    def __init__(self, max_logs: int = 200):
        self._logs: deque[RequestLog] = deque(maxlen=max_logs)
        self._lock = Lock()
        self.total_requests = 0
        self.total_errors = 0
        self.total_fallbacks = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.provider_counts: dict[str, int] = {}
        self.provider_errors: dict[str, int] = {}
        self.provider_latency_sum: dict[str, float] = {}
        self.intent_counts: dict[str, int] = {}
        self.cache_hits = 0
        self.cache_misses = 0
        self.total_retries = 0
        self.retry_successes = 0
        self.classifier_llm_success = 0
        self.classifier_regex_fallback = 0
        self.classifier_disagreements = 0
        # Compression cache tracking
        self.compression_cache_hits = 0
        self.compression_cache_misses = 0
        # Tool call quality counters
        self.tool_calls_native = 0
        self.tool_calls_xml_extracted = 0
        self.tool_calls_recovered = 0
        self.tool_calls_truncated = 0
        self.tool_calls_hallucinated = 0
        # Per-model event tracking
        self._model_events: dict[str, dict[str, int]] = {}
        self.analysis_enforcements = 0
        self.analysis_refinements = 0
        self.analysis_quality_sum: float = 0.0
        self.analysis_quality_count: int = 0
        # Cost tracking
        self.total_cost_usd: float = 0.0
        self.cost_by_model: dict[str, float] = {}
        self.cost_by_intent: dict[str, float] = {}
        # Thinking token tracking (DeepSeek Reasoner)
        self.total_thinking_tokens: int = 0
        # Phase tracking
        self.phase_counts: dict[str, int] = {}
        self.phase_quality_sum: dict[str, float] = {}
        self.phase_quality_count: dict[str, int] = {}
        # Classifier outcome accuracy: did the response behavior match the classified intent?
        self.intent_outcome_correct: int = 0
        self.intent_outcome_wrong: int = 0

    def record(self, log: RequestLog):
        with self._lock:
            self._logs.append(log)
            self.total_requests += 1
            self.total_input_tokens += log.input_tokens
            self.total_output_tokens += log.output_tokens
            p = log.provider
            self.provider_counts[p] = self.provider_counts.get(p, 0) + 1
            self.provider_latency_sum[p] = self.provider_latency_sum.get(p, 0) + log.latency_ms
            self.intent_counts[log.intent] = self.intent_counts.get(log.intent, 0) + 1
            if log.error:
                self.total_errors += 1
                self.provider_errors[p] = self.provider_errors.get(p, 0) + 1
            if log.is_fallback:
                self.total_fallbacks += 1
            if log.is_analysis:
                self.analysis_enforcements += 1
                self.analysis_refinements += log.refinement_attempts
                # Only include quality from non-streaming requests with actual output.
                # Streaming requests have output_tokens=0 at record time; their quality
                # is added later by update_streaming_log().
                if not log.is_stream and log.output_tokens > 0:
                    self.analysis_quality_sum += log.quality_score
                    self.analysis_quality_count += 1
            if log.cost_usd > 0:
                self.total_cost_usd += log.cost_usd
                model_key = log.model_used.split("/")[-1] if "/" in log.model_used else log.model_used
                self.cost_by_model[model_key] = self.cost_by_model.get(model_key, 0) + log.cost_usd
                self.cost_by_intent[log.intent] = self.cost_by_intent.get(log.intent, 0) + log.cost_usd
            # Phase tracking
            phase = log.phase
            self.phase_counts[phase] = self.phase_counts.get(phase, 0) + 1
            # Only include quality from non-streaming requests with actual output.
            # Streaming quality is added by update_streaming_log() after stream completes.
            if not log.is_stream and log.output_tokens > 0:
                self.phase_quality_sum[phase] = self.phase_quality_sum.get(phase, 0) + log.quality_score
                self.phase_quality_count[phase] = self.phase_quality_count.get(phase, 0) + 1

    def get_stats(self) -> dict:
        with self._lock:
            providers = {}
            for p in self.provider_counts:
                count = self.provider_counts[p]
                providers[p] = {
                    "requests": count,
                    "errors": self.provider_errors.get(p, 0),
                    "avg_latency_ms": round(self.provider_latency_sum.get(p, 0) / max(count, 1)),
                }
            return {
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "total_fallbacks": self.total_fallbacks,
                "fallback_rate_pct": round(self.total_fallbacks / max(self.total_requests, 1) * 100, 1),
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_thinking_tokens": self.total_thinking_tokens,
                "cache": {"hits": self.cache_hits, "misses": self.cache_misses},
                "compression_cache": {
                    "hits": self.compression_cache_hits,
                    "misses": self.compression_cache_misses,
                },
                "retries": {"total": self.total_retries, "successes": self.retry_successes},
                "classifier": {
                    "llm_success": self.classifier_llm_success,
                    "regex_fallback": self.classifier_regex_fallback,
                    "disagreements": self.classifier_disagreements,
                    # agreement_rate_pct: LLM-vs-regex coincidence (NOT accuracy).
                    # Low values are expected when user text contains ambiguous keywords
                    # (e.g. "fix" in analysis context → regex says BUILDING, LLM says ANALYZING).
                    "agreement_rate_pct": round(
                        (1 - self.classifier_disagreements / max(self.classifier_llm_success, 1)) * 100, 1
                    ),
                    # outcome_accuracy_pct: did the response behavior match the classified intent?
                    # This is the real accuracy signal — measures routing effectiveness.
                    "outcome_correct": self.intent_outcome_correct,
                    "outcome_wrong": self.intent_outcome_wrong,
                    "outcome_accuracy_pct": round(
                        self.intent_outcome_correct
                        / max(self.intent_outcome_correct + self.intent_outcome_wrong, 1)
                        * 100, 1
                    ),
                },
                "tool_quality": self._tool_quality_stats(),
                "model_quality": dict(self._model_events),
                "analysis_enforcements": self.analysis_enforcements,
                "analysis_refinements": self.analysis_refinements,
                "analysis_avg_quality": round(
                    self.analysis_quality_sum / max(self.analysis_quality_count, 1), 2
                ),
                "cost": {
                    "total_usd": round(self.total_cost_usd, 6),
                    "by_model": {k: round(v, 6) for k, v in self.cost_by_model.items()},
                    "by_intent": {k: round(v, 6) for k, v in self.cost_by_intent.items()},
                    "avg_per_request": round(
                        self.total_cost_usd / max(self.total_requests, 1), 6
                    ),
                },
                "providers": providers,
                "intents": dict(self.intent_counts),
                "quality_by_phase": {
                    phase: {
                        "count": self.phase_counts.get(phase, 0),
                        "avg_quality": round(
                            self.phase_quality_sum.get(phase, 0) / max(self.phase_quality_count.get(phase, 0), 1), 2
                        ),
                    }
                    for phase in self.phase_counts
                },
            }

    def update_streaming_log(
        self,
        output_tokens: int = 0,
        quality_score: float = 1.0,
        quality_issues: list[str] | None = None,
        cost_usd: float = 0.0,
        thinking_tokens: int = 0,
    ) -> None:
        """Update the most recent streaming RequestLog with post-stream data.

        Called after a streaming response completes to fill in tokens, quality,
        and cost that aren't available until the stream finishes.
        """
        with self._lock:
            if not self._logs:
                return
            log = self._logs[-1]
            if not log.is_stream:
                return
            # Update token totals
            self.total_output_tokens += output_tokens
            log.output_tokens = output_tokens
            # Update quality
            log.quality_score = quality_score
            if log.is_analysis:
                # record() skips streaming requests, so add quality directly here
                self.analysis_quality_sum += quality_score
                self.analysis_quality_count += 1
            # Phase tracking (record() skips streaming, so add here)
            phase = log.phase
            self.phase_quality_sum[phase] = self.phase_quality_sum.get(phase, 0) + quality_score
            self.phase_quality_count[phase] = self.phase_quality_count.get(phase, 0) + 1
            # Update thinking tokens
            if thinking_tokens > 0:
                self.total_thinking_tokens += thinking_tokens
            # Update cost
            if cost_usd > 0:
                log.cost_usd = cost_usd
                self.total_cost_usd += cost_usd
                model_key = log.model_used.split("/")[-1] if "/" in log.model_used else log.model_used
                self.cost_by_model[model_key] = self.cost_by_model.get(model_key, 0) + cost_usd
                self.cost_by_intent[log.intent] = self.cost_by_intent.get(log.intent, 0) + cost_usd

    def get_recent(self, n: int = 50) -> list[dict]:
        with self._lock:
            return [asdict(log) for log in list(self._logs)[-n:]]

    def increment_cache_hit(self):
        with self._lock:
            self.cache_hits += 1

    def increment_cache_miss(self):
        with self._lock:
            self.cache_misses += 1

    def increment_classifier_disagreement(self) -> None:
        with self._lock:
            self.classifier_disagreements += 1

    def increment_intent_outcome_correct(self) -> None:
        with self._lock:
            self.intent_outcome_correct += 1

    def increment_intent_outcome_wrong(self) -> None:
        with self._lock:
            self.intent_outcome_wrong += 1

    def increment_tool_counter(self, counter: str) -> None:
        """Increment a tool quality counter by name.

        Valid counters: native, xml_extracted, recovered, truncated, hallucinated.
        """
        attr = f"tool_calls_{counter}"
        with self._lock:
            setattr(self, attr, getattr(self, attr, 0) + 1)

    def record_model_event(self, model: str, event: str) -> None:
        """Track per-model events: 'tool_success', 'tool_failure', 'recovery', 'truncation'."""
        with self._lock:
            key = model.split("/")[-1] if "/" in model else model
            bucket = self._model_events.setdefault(key, {})
            bucket[event] = bucket.get(event, 0) + 1

    def _tool_quality_stats(self) -> dict:
        """Compute tool quality stats (called inside _lock)."""
        native = self.tool_calls_native
        xml = self.tool_calls_xml_extracted
        recovered = self.tool_calls_recovered
        truncated = self.tool_calls_truncated
        hallucinated = self.tool_calls_hallucinated
        total = native + xml + recovered + truncated + hallucinated
        return {
            "native": native,
            "xml_extracted": xml,
            "recovered": recovered,
            "truncated": truncated,
            "hallucinated": hallucinated,
            "total": total,
            "success_rate_pct": round((native + xml + recovered) / max(total, 1) * 100, 1),
        }


# Singleton
metrics = ProxyMetrics()
