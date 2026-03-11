# tests/test_tool_validation.py
"""Tests for hallucinated native tool name validation in streaming and non-streaming paths.

Bug #3: Native tool call paths (LiteLLM streaming + non-streaming) call validate_tool_name()
but previously ignored the result — hallucinated tool names were logged but still emitted.
These tests verify the fixed behavior: hallucinated tools are skipped, valid tools pass through.
"""
import pytest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Shared fixtures ────────────────────────────────────────────────────

TOOLS = [
    {"name": "Bash", "description": "Run command", "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }},
    {"name": "Read", "description": "Read file", "input_schema": {
        "type": "object",
        "properties": {"file_path": {"type": "string"}},
        "required": ["file_path"],
    }},
]


def _make_tool_call_obj(name: str, arguments: str, tool_id: str = "call_abc", index: int = 0):
    """Build a mock LiteLLM tool_call object."""
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=tool_id, function=fn)


def _make_litellm_response(tool_calls: list, model: str = "minimax/MiniMax-Text-01"):
    """Build a minimal mock LiteLLM completion response dict."""
    return {
        "id": "resp-test",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        "model": model,
    }


def _make_request(tools=None):
    """Build a minimal MessagesRequest-like namespace."""
    return SimpleNamespace(
        model="claude-sonnet-4-5-20250929",
        tools=tools or TOOLS,
        max_tokens=1000,
        stream=False,
        system=None,
        messages=[{"role": "user", "content": "hi"}],
    )


# ═══════════════════════════════════════════════════════════════════════
# Test C — Non-streaming path (converters.py)
# ═══════════════════════════════════════════════════════════════════════

class TestNonStreamingHallucinatedToolValidation:
    """convert_litellm_to_anthropic() must drop hallucinated tool names."""

    def test_valid_tool_passes_through(self):
        """A tool name matching the schema is included in the response."""
        from llm.converters import convert_litellm_to_anthropic

        tc = _make_tool_call_obj("Bash", '{"command": "ls"}')
        response = _make_litellm_response([tc])
        request = _make_request()

        with patch("llm.converters.is_no_tools_model", return_value=False):
            result = convert_litellm_to_anthropic(response, request)

        tool_blocks = [b for b in result.content if b.type == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].name == "Bash"

    def test_hallucinated_tool_is_skipped(self):
        """A tool name NOT in the schema is dropped — not emitted to Claude Code."""
        from llm.converters import convert_litellm_to_anthropic

        tc = _make_tool_call_obj("NonExistentHallucinatedTool", '{"x": 1}')
        response = _make_litellm_response([tc])
        request = _make_request()

        with patch("llm.converters.is_no_tools_model", return_value=False):
            result = convert_litellm_to_anthropic(response, request)

        tool_blocks = [b for b in result.content if b.type == "tool_use"]
        assert len(tool_blocks) == 0, (
            f"Hallucinated tool should be dropped, but got: {tool_blocks}"
        )

    def test_valid_tool_after_hallucinated_is_preserved(self):
        """If first tool is hallucinated and second is valid, only the valid one is emitted."""
        from llm.converters import convert_litellm_to_anthropic

        bad_tc = _make_tool_call_obj("HallucinatedTool", '{"x": 1}', tool_id="call_bad", index=0)
        good_tc = _make_tool_call_obj("Read", '{"file_path": "/a.py"}', tool_id="call_good", index=1)
        response = _make_litellm_response([bad_tc, good_tc])
        request = _make_request()

        with patch("llm.converters.is_no_tools_model", return_value=False):
            result = convert_litellm_to_anthropic(response, request)

        tool_blocks = [b for b in result.content if b.type == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].name == "Read"

    def test_hallucinated_tool_increments_hallucinated_counter(self):
        """Hallucinated tool increments the 'hallucinated' metrics counter."""
        from llm.converters import convert_litellm_to_anthropic
        from utils.metrics import metrics

        tc = _make_tool_call_obj("FakeTool", '{"a": 1}')
        response = _make_litellm_response([tc])
        request = _make_request()

        before = getattr(metrics, "tool_calls_hallucinated", 0)
        with patch("llm.converters.is_no_tools_model", return_value=False):
            convert_litellm_to_anthropic(response, request)
        after = getattr(metrics, "tool_calls_hallucinated", 0)

        assert after > before, "Hallucinated counter should increment"


# ═══════════════════════════════════════════════════════════════════════
# Test B — Streaming path (streaming.py _StreamCtx)
# ═══════════════════════════════════════════════════════════════════════

class TestStreamingHallucinatedToolValidation:
    """_StreamCtx.hallucinated_tool_indices prevents hallucinated tools from streaming."""

    def test_hallucinated_tool_indices_field_exists(self):
        """_StreamCtx dataclass has hallucinated_tool_indices field."""
        from llm.streaming import _StreamCtx
        from llm.transformers.universal_tool_extraction import XmlToolBuffer
        from utils.tool_utils import build_valid_tool_names as _build_valid_tool_names
        import dataclasses

        fields = {f.name for f in dataclasses.fields(_StreamCtx)}
        assert "hallucinated_tool_indices" in fields, (
            "_StreamCtx must have hallucinated_tool_indices field for streaming hallucination guard"
        )

    def test_hallucinated_tool_indices_default_is_empty_set(self):
        """hallucinated_tool_indices defaults to an empty set (not shared across instances)."""
        from llm.streaming import _StreamCtx
        from llm.transformers.universal_tool_extraction import XmlToolBuffer
        from utils.tool_utils import build_valid_tool_names as _build_valid_tool_names

        valid = _build_valid_tool_names(TOOLS)
        buf = XmlToolBuffer(valid_tool_names=valid, tools=TOOLS)

        ctx1 = _StreamCtx(no_tools_mode=False, request_tools=TOOLS, valid_names=valid, xml_tool_buffer=buf)
        ctx2 = _StreamCtx(no_tools_mode=False, request_tools=TOOLS, valid_names=valid, xml_tool_buffer=buf)

        assert ctx1.hallucinated_tool_indices == set()
        assert ctx2.hallucinated_tool_indices == set()
        # Ensure they're separate instances (not shared mutable default)
        ctx1.hallucinated_tool_indices.add(0)
        assert 0 not in ctx2.hallucinated_tool_indices, (
            "hallucinated_tool_indices must not be shared between _StreamCtx instances"
        )

    @pytest.mark.asyncio
    async def test_hallucinated_tool_not_yielded_in_streaming(self):
        """handle_streaming() does not yield content_block_start for hallucinated tool name."""
        from llm.streaming import handle_streaming

        # Build a fake streaming response with a single hallucinated tool call
        def make_chunk(name=None, arguments=None, finish_reason=None):
            tool_call = None
            if name is not None or arguments is not None:
                fn = SimpleNamespace(name=name or "", arguments=arguments or "")
                tool_call = SimpleNamespace(index=0, id="call_xyz", function=fn)
            delta = SimpleNamespace(
                content=None,
                tool_calls=[tool_call] if tool_call else None,
                reasoning_content=None,
            )
            choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
            chunk = SimpleNamespace(choices=[choice], model="minimax/MiniMax-Text-01")
            return chunk

        async def fake_stream():
            yield make_chunk(name="HallucinatedTool", arguments="")
            yield make_chunk(arguments='{"x": 1}')
            yield make_chunk(finish_reason="tool_calls")

        request = _make_request()
        events = []
        async for event in handle_streaming(
            response_generator=fake_stream(),
            original_request=request,
        ):
            events.append(event)

        # No content_block_start with type=tool_use should appear
        tool_start_events = [
            e for e in events
            if isinstance(e, str) and '"tool_use"' in e and "content_block_start" in e
        ]
        assert len(tool_start_events) == 0, (
            f"Hallucinated tool should not produce content_block_start events, got: {tool_start_events}"
        )

    @pytest.mark.asyncio
    async def test_valid_tool_after_hallucinated_is_yielded(self):
        """Valid tool (index=1) after hallucinated (index=0) is correctly emitted."""
        from llm.streaming import handle_streaming

        def make_chunk(index=0, name=None, arguments=None, finish_reason=None):
            tool_call = None
            if name is not None or arguments is not None:
                fn = SimpleNamespace(name=name or "", arguments=arguments or "")
                tool_call = SimpleNamespace(index=index, id=f"call_{index}", function=fn)
            delta = SimpleNamespace(
                content=None,
                tool_calls=[tool_call] if tool_call else None,
                reasoning_content=None,
            )
            choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
            return SimpleNamespace(choices=[choice], model="minimax/MiniMax-Text-01")

        async def fake_stream():
            yield make_chunk(index=0, name="HallucinatedTool", arguments="")
            yield make_chunk(index=0, arguments='{"x": 1}')       # arg chunk for hallucinated
            yield make_chunk(index=1, name="Bash", arguments="")   # valid second tool
            yield make_chunk(index=1, arguments='{"command": "ls"}')
            yield make_chunk(finish_reason="tool_calls")

        request = _make_request()
        events = []
        async for event in handle_streaming(
            response_generator=fake_stream(),
            original_request=request,
        ):
            events.append(event)

        # Bash tool_use block should appear
        tool_start_events = [
            e for e in events
            if isinstance(e, str) and '"tool_use"' in e and "content_block_start" in e
        ]
        assert len(tool_start_events) >= 1, (
            f"Valid tool Bash should produce a content_block_start event"
        )
        bash_events = [e for e in tool_start_events if "Bash" in e]
        assert len(bash_events) >= 1, f"Expected Bash in events, got: {tool_start_events}"
