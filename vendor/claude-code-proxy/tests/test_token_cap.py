# tests/test_token_cap.py
"""Tests for TokenCapTransformer + provider_cap_for_base_url."""
import pytest
from types import SimpleNamespace

from llm.pipeline import TransformContext
from llm.transformers.token_cap import TokenCapTransformer, provider_cap_for_base_url
from config import PolicyConfig


def _policy(max_input=0, hard_block=False):
    return PolicyConfig(
        tool_allowlist_raw="*", policy_note_in_system=True,
        max_input_tokens=max_input, hard_block_oversize=hard_block,
        analysis_enforcement=False, tool_upgrade_threshold=5,
        guard_system="",
    )


class TestProviderCapForBaseUrl:

    def test_groq(self):
        assert provider_cap_for_base_url("https://api.groq.com/v1") == 5500

    def test_groq_subdomain(self):
        assert provider_cap_for_base_url("https://groq.com/api") == 5500

    def test_ollama(self):
        assert provider_cap_for_base_url("http://localhost:11434") == 25000

    def test_no_cap(self):
        assert provider_cap_for_base_url("https://api.z.ai/v4") == 0

    def test_none(self):
        assert provider_cap_for_base_url(None) == 0

    def test_empty_string(self):
        assert provider_cap_for_base_url("") == 0


class TestTokenCapTransformer:

    @pytest.mark.asyncio
    async def test_sets_approx_tokens(self):
        t = TokenCapTransformer(_policy(), base_url=None)
        ctx = TransformContext(raw_body=b"x" * 600)
        await t.transform(SimpleNamespace(), ctx)
        assert ctx.approx_tokens == 100  # 600 / 6

    @pytest.mark.asyncio
    async def test_empty_body(self):
        t = TokenCapTransformer(_policy(), base_url=None)
        ctx = TransformContext(raw_body=b"")
        await t.transform(SimpleNamespace(), ctx)
        assert ctx.approx_tokens == 1  # max(1, 0//6)

    @pytest.mark.asyncio
    async def test_provider_cap_raises_when_hard_block(self):
        """Groq cap=5500, body size gives approx_tokens > 5500."""
        body = b"x" * (5501 * 6)  # approx_tokens = 5501
        t = TokenCapTransformer(_policy(hard_block=True), base_url="https://api.groq.com/v1")
        ctx = TransformContext(raw_body=body)
        with pytest.raises(ValueError, match="Provider cap exceeded"):
            await t.transform(SimpleNamespace(), ctx)

    @pytest.mark.asyncio
    async def test_provider_cap_no_raise_when_soft(self):
        body = b"x" * (5501 * 6)
        t = TokenCapTransformer(_policy(hard_block=False), base_url="https://api.groq.com/v1")
        ctx = TransformContext(raw_body=body)
        await t.transform(SimpleNamespace(), ctx)  # should not raise
        assert ctx.approx_tokens > 5500

    @pytest.mark.asyncio
    async def test_hard_cap_raises(self):
        body = b"x" * (1001 * 6)  # approx_tokens = 1001
        t = TokenCapTransformer(_policy(max_input=1000, hard_block=True), base_url=None)
        ctx = TransformContext(raw_body=body)
        with pytest.raises(ValueError, match="Oversize request"):
            await t.transform(SimpleNamespace(), ctx)

    @pytest.mark.asyncio
    async def test_hard_cap_no_raise_when_soft(self):
        body = b"x" * (1001 * 6)
        t = TokenCapTransformer(_policy(max_input=1000, hard_block=False), base_url=None)
        ctx = TransformContext(raw_body=body)
        await t.transform(SimpleNamespace(), ctx)  # should not raise

    @pytest.mark.asyncio
    async def test_under_all_caps(self):
        body = b"x" * (100 * 6)  # approx_tokens = 100
        t = TokenCapTransformer(
            _policy(max_input=1000, hard_block=True),
            base_url="https://api.groq.com/v1",
        )
        ctx = TransformContext(raw_body=body)
        await t.transform(SimpleNamespace(), ctx)
        assert ctx.approx_tokens == 100

    def test_name(self):
        assert TokenCapTransformer(_policy(), None).name == "token_cap"
