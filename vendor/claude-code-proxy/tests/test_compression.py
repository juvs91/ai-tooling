# tests/test_compression.py
"""Tests for CompressionTransformer."""
import pytest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from llm.pipeline import TransformContext
from llm.transformers.compression import CompressionTransformer
from config import CompressorConfig, ModelRouting


def _comp_cfg(model="openai/deepseek-chat", api_key="k", base_url="http://x",
              keep_recent=15, trigger_ratio=0.85,
              fb_model=None, fb_key=None, fb_base=None):
    return CompressorConfig(
        model=model, api_key=api_key, base_url=base_url,
        keep_recent=keep_recent, trigger_ratio=trigger_ratio,
        fallback_model=fb_model, fallback_api_key=fb_key, fallback_base_url=fb_base,
    )


def _routing(ctx_window=200000, max_out=8192, preferred_provider="openai", big_model="m"):
    return ModelRouting(
        preferred_provider=preferred_provider, small_model="m", big_model=big_model,
        building_model="m", model_context_window=ctx_window,
        max_output_tokens=max_out, reasoning_max_tokens=16000,
    )


def _request(model="openai/glm-4.7", max_tokens=4096):
    return SimpleNamespace(model=model, max_tokens=max_tokens)


class TestCCCompactDetection:
    """CC compact requests (containing 'partial transcript') bypass proxy compression
    and are routed directly to big_model for proper <summary></summary> generation."""

    @pytest.mark.asyncio
    async def test_compact_request_skips_compression(self):
        """A message with 'partial transcript' must skip compress_messages_if_needed."""
        t = CompressionTransformer(_comp_cfg(), _routing(preferred_provider="anthropic", big_model="glm-4.7"))
        ctx = TransformContext(litellm_request={
            "messages": [
                {"role": "user", "content": "You have written a partial transcript for the initial task above. Please write a summary..."},
            ],
            "model": "anthropic/glm-4.7",
        })
        with patch("llm.transformers.compression.compress_messages_if_needed") as mock:
            await t.transform(_request(), ctx)
            mock.assert_not_called()
        assert ctx.was_compressed is False

    @pytest.mark.asyncio
    async def test_compact_forces_big_model(self):
        """CC compact must override ctx.litellm_request['model'] to big_model."""
        t = CompressionTransformer(_comp_cfg(), _routing(preferred_provider="anthropic", big_model="glm-4.7"))
        ctx = TransformContext(litellm_request={
            "messages": [{"role": "user", "content": "partial transcript to summarize"}],
            "model": "openai/deepseek-chat",
        })
        await t.transform(_request(), ctx)
        assert ctx.litellm_request["model"] == "anthropic/glm-4.7"

    @pytest.mark.asyncio
    async def test_compact_case_insensitive(self):
        """Detection is case-insensitive."""
        t = CompressionTransformer(_comp_cfg(), _routing(preferred_provider="anthropic", big_model="glm-4.7"))
        ctx = TransformContext(litellm_request={
            "messages": [{"role": "user", "content": "PARTIAL TRANSCRIPT above..."}],
            "model": "openai/deepseek-chat",
        })
        await t.transform(_request(), ctx)
        assert ctx.litellm_request["model"] == "anthropic/glm-4.7"

    @pytest.mark.asyncio
    async def test_compact_with_list_content(self):
        """Detection works when message content is a list of blocks."""
        t = CompressionTransformer(_comp_cfg(), _routing(preferred_provider="anthropic", big_model="glm-4.7"))
        ctx = TransformContext(litellm_request={
            "messages": [{"role": "user", "content": [{"type": "text", "text": "partial transcript here"}]}],
            "model": "openai/deepseek-chat",
        })
        await t.transform(_request(), ctx)
        assert ctx.litellm_request["model"] == "anthropic/glm-4.7"

    @pytest.mark.asyncio
    async def test_non_compact_still_compresses(self):
        """Regular messages (no 'partial transcript') go through normal compression."""
        messages = [{"role": "user", "content": "help me fix a bug"}]
        t = CompressionTransformer(_comp_cfg(), _routing(preferred_provider="anthropic", big_model="glm-4.7"))
        ctx = TransformContext(litellm_request={"messages": messages, "tools": None})
        with patch("llm.transformers.compression.compress_messages_if_needed",
                    new_callable=AsyncMock, return_value=(messages, False)) as mock:
            await t.transform(_request(), ctx)
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_compact_only_checks_user_messages(self):
        """'partial transcript' in an assistant message should NOT trigger compact detection."""
        messages = [
            {"role": "assistant", "content": "I wrote a partial transcript earlier"},
            {"role": "user", "content": "continue"},
        ]
        t = CompressionTransformer(_comp_cfg(), _routing(preferred_provider="anthropic", big_model="glm-4.7"))
        ctx = TransformContext(litellm_request={"messages": messages, "tools": None, "model": "openai/x"})
        with patch("llm.transformers.compression.compress_messages_if_needed",
                    new_callable=AsyncMock, return_value=(messages, False)) as mock:
            await t.transform(_request(), ctx)
            mock.assert_called_once()
            # model should NOT have been overridden to big_model
            assert ctx.litellm_request["model"] == "openai/x"


class TestCompressionSkipConditions:

    @pytest.mark.asyncio
    async def test_skips_when_no_context_window(self):
        t = CompressionTransformer(_comp_cfg(), _routing(ctx_window=0))
        ctx = TransformContext(litellm_request={"messages": [{"role": "user", "content": "hi"}]})
        with patch("llm.transformers.compression.compress_messages_if_needed") as mock:
            await t.transform(_request(), ctx)
            mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_compressor_model(self):
        t = CompressionTransformer(_comp_cfg(model=""), _routing())
        ctx = TransformContext(litellm_request={"messages": []})
        with patch("llm.transformers.compression.compress_messages_if_needed") as mock:
            await t.transform(_request(), ctx)
            mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_api_key(self):
        t = CompressionTransformer(_comp_cfg(api_key=""), _routing())
        ctx = TransformContext(litellm_request={"messages": []})
        with patch("llm.transformers.compression.compress_messages_if_needed") as mock:
            await t.transform(_request(), ctx)
            mock.assert_not_called()


class TestCompressionExecution:

    @pytest.mark.asyncio
    async def test_calls_compressor(self):
        messages = [{"role": "user", "content": "hi"}]
        compressed = [{"role": "user", "content": "compressed"}]

        t = CompressionTransformer(_comp_cfg(), _routing())
        ctx = TransformContext(litellm_request={"messages": messages, "tools": None})

        with patch("llm.transformers.compression.compress_messages_if_needed",
                    new_callable=AsyncMock, return_value=(compressed, False)):
            await t.transform(_request(), ctx)
            assert ctx.litellm_request["messages"] == compressed
            assert ctx.was_compressed is False

    @pytest.mark.asyncio
    async def test_sets_was_compressed(self):
        t = CompressionTransformer(_comp_cfg(), _routing())
        ctx = TransformContext(litellm_request={
            "messages": [{"role": "user", "content": "hi"}],
            "tools": None,
        })

        with patch("llm.transformers.compression.compress_messages_if_needed",
                    new_callable=AsyncMock, return_value=([], True)):
            await t.transform(_request(), ctx)
            assert ctx.was_compressed is True


class TestMaxTokensRecap:

    @pytest.mark.asyncio
    async def test_recaps_after_compression(self):
        """After compression, max_completion_tokens should be recalculated."""
        messages = [{"role": "user", "content": "x" * 1000}]
        t = CompressionTransformer(_comp_cfg(), _routing(ctx_window=200000, max_out=8192))
        ctx = TransformContext(litellm_request={
            "messages": messages,
            "tools": None,
            "max_completion_tokens": 16384,
        })

        with patch("llm.transformers.compression.compress_messages_if_needed",
                    new_callable=AsyncMock, return_value=(messages, True)), \
             patch("llm.transformers.compression.is_no_tools_model", return_value=False):
            await t.transform(_request(model="openai/glm-4.7", max_tokens=8192), ctx)
            assert "max_completion_tokens" in ctx.litellm_request
            assert ctx.litellm_request["max_completion_tokens"] >= 1024

    @pytest.mark.asyncio
    async def test_recap_for_no_tools_model_applies_reasoning_cap(self):
        """no_tools models get recapped to reasoning_max_tokens after compression."""
        messages = [{"role": "user", "content": "hi"}]
        t = CompressionTransformer(_comp_cfg(), _routing())
        ctx = TransformContext(litellm_request={
            "messages": messages,
            "tools": None,
            "max_completion_tokens": 32000,
        })

        with patch("llm.transformers.compression.compress_messages_if_needed",
                    new_callable=AsyncMock, return_value=(messages, True)), \
             patch("llm.transformers.compression.is_no_tools_model", return_value=True):
            await t.transform(_request(model="openai/deepseek-reasoner", max_tokens=32000), ctx)
            # recapped to reasoning_max_tokens (16000)
            assert ctx.litellm_request["max_completion_tokens"] == 16000

    @pytest.mark.asyncio
    async def test_no_recap_when_not_compressed(self):
        messages = [{"role": "user", "content": "hi"}]
        t = CompressionTransformer(_comp_cfg(), _routing())
        ctx = TransformContext(litellm_request={
            "messages": messages,
            "tools": None,
            "max_completion_tokens": 16384,
        })

        with patch("llm.transformers.compression.compress_messages_if_needed",
                    new_callable=AsyncMock, return_value=(messages, False)):
            await t.transform(_request(), ctx)
            assert ctx.litellm_request["max_completion_tokens"] == 16384

    def test_name(self):
        assert CompressionTransformer(_comp_cfg(), _routing()).name == "compression"


class TestEffectiveContextWindow:
    """Fix 4: CompressionTransformer should use ctx.effective_context_window when set."""

    @pytest.mark.asyncio
    async def test_uses_effective_context_window_over_global(self):
        """When effective_context_window is set, it should be passed to compress_messages_if_needed."""
        messages = [{"role": "user", "content": "hi"}]
        t = CompressionTransformer(_comp_cfg(), _routing(ctx_window=200000))
        ctx = TransformContext(
            litellm_request={"messages": messages, "tools": None},
            effective_context_window=64000,
        )

        with patch("llm.transformers.compression.compress_messages_if_needed",
                    new_callable=AsyncMock, return_value=(messages, False)) as mock:
            await t.transform(_request(), ctx)
            assert mock.call_args.kwargs["model_context_window"] == 64000

    @pytest.mark.asyncio
    async def test_falls_back_to_global_when_effective_is_zero(self):
        """When effective_context_window is 0, use global model_context_window."""
        messages = [{"role": "user", "content": "hi"}]
        t = CompressionTransformer(_comp_cfg(), _routing(ctx_window=200000))
        ctx = TransformContext(
            litellm_request={"messages": messages, "tools": None},
            effective_context_window=0,
        )

        with patch("llm.transformers.compression.compress_messages_if_needed",
                    new_callable=AsyncMock, return_value=(messages, False)) as mock:
            await t.transform(_request(), ctx)
            assert mock.call_args.kwargs["model_context_window"] == 200000
