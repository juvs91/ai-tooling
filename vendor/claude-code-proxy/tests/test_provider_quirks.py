# tests/test_provider_quirks.py
"""Tests for ProviderQuirksTransformer."""
import pytest
from types import SimpleNamespace

from llm.pipeline import TransformContext
from llm.transformers.provider_quirks import ProviderQuirksTransformer


class TestProviderQuirksTransformer:

    @pytest.mark.asyncio
    async def test_injects_extra_body_when_streaming_with_tools(self):
        extra = {"tool_stream": True}
        t = ProviderQuirksTransformer(extra)
        ctx = TransformContext(litellm_request={
            "stream": True,
            "tools": [{"type": "function", "function": {"name": "Read"}}],
        })
        await t.transform(SimpleNamespace(), ctx)
        assert ctx.litellm_request["extra_body"] == {"tool_stream": True}

    @pytest.mark.asyncio
    async def test_no_inject_when_not_streaming(self):
        t = ProviderQuirksTransformer({"tool_stream": True})
        ctx = TransformContext(litellm_request={
            "stream": False,
            "tools": [{"type": "function", "function": {"name": "Read"}}],
        })
        await t.transform(SimpleNamespace(), ctx)
        assert "extra_body" not in ctx.litellm_request

    @pytest.mark.asyncio
    async def test_no_inject_when_no_tools(self):
        t = ProviderQuirksTransformer({"tool_stream": True})
        ctx = TransformContext(litellm_request={"stream": True, "tools": None})
        await t.transform(SimpleNamespace(), ctx)
        assert "extra_body" not in ctx.litellm_request

    @pytest.mark.asyncio
    async def test_no_inject_when_tools_empty(self):
        t = ProviderQuirksTransformer({"tool_stream": True})
        ctx = TransformContext(litellm_request={"stream": True, "tools": []})
        await t.transform(SimpleNamespace(), ctx)
        assert "extra_body" not in ctx.litellm_request

    @pytest.mark.asyncio
    async def test_no_inject_when_extra_is_none(self):
        t = ProviderQuirksTransformer(None)
        ctx = TransformContext(litellm_request={
            "stream": True,
            "tools": [{"type": "function", "function": {"name": "Read"}}],
        })
        await t.transform(SimpleNamespace(), ctx)
        assert "extra_body" not in ctx.litellm_request

    @pytest.mark.asyncio
    async def test_merges_with_existing_extra_body(self):
        t = ProviderQuirksTransformer({"tool_stream": True})
        ctx = TransformContext(litellm_request={
            "stream": True,
            "tools": [{"type": "function", "function": {"name": "Read"}}],
            "extra_body": {"existing_key": "value"},
        })
        await t.transform(SimpleNamespace(), ctx)
        assert ctx.litellm_request["extra_body"] == {"existing_key": "value", "tool_stream": True}

    def test_name(self):
        assert ProviderQuirksTransformer(None).name == "provider_quirks"
