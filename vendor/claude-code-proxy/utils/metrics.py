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
                "cache": {"hits": self.cache_hits, "misses": self.cache_misses},
                "retries": {"total": self.total_retries, "successes": self.retry_successes},
                "classifier": {"llm_success": self.classifier_llm_success, "regex_fallback": self.classifier_regex_fallback},
                "providers": providers,
                "intents": dict(self.intent_counts),
            }

    def get_recent(self, n: int = 50) -> list[dict]:
        with self._lock:
            return [asdict(log) for log in list(self._logs)[-n:]]


# Singleton
metrics = ProxyMetrics()
