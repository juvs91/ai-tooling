# tests/test_concurrency.py
"""Concurrency regression tests for shared mutable state in the proxy.

Covers:
- Fix 1a: _per_msg_cache thread-safety (utils/utils.py)
- Fix 1b: _gemini_schema_cache / _tool_conversion_cache thread-safety (llm/converters.py)
- Fix 1c: provider_quirks does NOT mutate original message dicts
- Fix 1d: pipeline cache built once under concurrent calls (@lru_cache)
"""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Fix 1a: Token Cache ────────────────────────────────────────────────

class TestTokenCacheThreadSafety:
    """_per_msg_cache must not raise KeyError or corrupt under concurrent access."""

    def test_concurrent_store_and_read_no_exception(self):
        """Concurrent store+read on distinct keys must not raise."""
        from utils.utils import store_token_count, cached_token_count, _per_msg_cache

        errors = []

        def worker(i):
            try:
                msgs = [{"role": "user", "content": f"msg-{i}"}]
                store_token_count(msgs, "glm-4.7", 10 + i)
                cached_token_count(msgs, "glm-4.7")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Exceptions in concurrent token cache: {errors}"

    def test_eviction_under_concurrency_no_exception(self):
        """Eviction loop must not raise RuntimeError due to dict size change during iteration."""
        from utils.utils import store_token_count, _PER_MSG_MAX

        errors = []

        def writer(i):
            try:
                for j in range(10):
                    msgs = [{"role": "user", "content": f"evict-{i}-{j}"}]
                    store_token_count(msgs, "glm-4.7", 5)
            except Exception as e:
                errors.append(e)

        # Flood with many unique messages to trigger eviction
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Eviction raised exceptions: {errors}"

    def test_store_then_read_returns_correct_value(self):
        """After storing, cached_token_count must return a consistent non-None value."""
        from utils.utils import store_token_count, cached_token_count

        msgs = [{"role": "user", "content": "hello-consistency-check"}]
        store_token_count(msgs, "test-model", 100)
        result = cached_token_count(msgs, "test-model")
        assert result is not None
        assert result > 0


# ── Fix 1b: Schema/Tool Cache ──────────────────────────────────────────

class TestSchemaCacheThreadSafety:
    """_gemini_schema_cache and _tool_conversion_cache must not corrupt under concurrency."""

    def test_concurrent_tool_conversion_no_exception(self):
        """Concurrent _convert_tool_cached calls must not raise or return None."""
        from llm.converters import _convert_tool_cached

        tool = {
            "name": "Bash",
            "description": "Run command",
            "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
        }
        results = []
        errors = []

        def worker():
            try:
                result = _convert_tool_cached(tool, is_gemini=False)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Exceptions in concurrent tool cache: {errors}"
        assert all(r["function"]["name"] == "Bash" for r in results)

    def test_concurrent_gemini_schema_no_exception(self):
        """Concurrent clean_gemini_schema_cached calls must not raise."""
        from llm.converters import clean_gemini_schema_cached

        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        errors = []

        def worker():
            try:
                clean_gemini_schema_cached(schema)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Exceptions in concurrent schema cache: {errors}"

    def test_tool_conversion_result_consistent(self):
        """All concurrent calls for same tool must return identical result."""
        from llm.converters import _convert_tool_cached

        tool = {
            "name": "Read",
            "description": "Read file",
            "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}},
        }
        results = []

        def worker():
            results.append(_convert_tool_cached(tool, is_gemini=False))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should return the exact same content
        assert all(r == results[0] for r in results), "Inconsistent results across concurrent calls"


# ── Fix 1c: Message Dict Mutation ─────────────────────────────────────

class TestProviderQuirksMutation:
    """ProviderQuirksTransformer must NOT mutate the original message dicts."""

    @pytest.mark.asyncio
    async def test_reasoning_content_injection_copies_not_mutates(self):
        """Original message dict must be unchanged after reasoning_content injection."""
        from llm.transformers.provider_quirks import ProviderQuirksTransformer

        original_msg = {"role": "assistant", "content": "I will help."}
        original_id = id(original_msg)

        ctx = SimpleNamespace(
            litellm_request={
                "messages": [original_msg],
                "stream": False,
                "tools": None,
            }
        )
        request = SimpleNamespace(model="deepseek/deepseek-reasoner")

        transformer = ProviderQuirksTransformer(stream_extra_body=None)
        await transformer.transform(request, ctx)

        # Original dict must not be modified
        assert "reasoning_content" not in original_msg, (
            "ProviderQuirksTransformer must not mutate the original message dict"
        )
        # But the ctx.litellm_request messages should have reasoning_content
        assert "reasoning_content" in ctx.litellm_request["messages"][0], (
            "reasoning_content should be in the transformed messages list"
        )

    @pytest.mark.asyncio
    async def test_non_reasoning_model_leaves_messages_unchanged(self):
        """Non-reasoning model: messages list must be untouched."""
        from llm.transformers.provider_quirks import ProviderQuirksTransformer

        original_msg = {"role": "assistant", "content": "hello"}
        ctx = SimpleNamespace(
            litellm_request={
                "messages": [original_msg],
                "stream": False,
                "tools": None,
            }
        )
        request = SimpleNamespace(model="openai/glm-4.7")
        transformer = ProviderQuirksTransformer(stream_extra_body=None)
        await transformer.transform(request, ctx)

        assert ctx.litellm_request["messages"][0] is original_msg, (
            "Non-reasoning model must not touch messages"
        )


# ── Fix 1d: Pipeline Cache ─────────────────────────────────────────────

class TestPipelineCacheSingleton:
    """_get_litellm_pipeline must return the same instance under concurrent calls."""

    def test_pipeline_built_once_concurrent(self):
        """Multiple concurrent calls must return the identical Pipeline object."""
        import proxy.proxy as proxy_mod
        from proxy.proxy import _get_litellm_pipeline
        from config import load_config

        # Reset the cache so we get a clean test
        proxy_mod._litellm_pipeline_cache = None
        cfg = load_config()
        results = []
        errors = []

        def worker():
            try:
                results.append(_get_litellm_pipeline(cfg))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors in pipeline cache test: {errors}"
        assert len(set(id(r) for r in results)) == 1, (
            "Pipeline must be a singleton — all concurrent calls should return the same object"
        )
