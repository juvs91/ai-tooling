# tests/test_passthrough_xml_tool.py
"""Tests for passthrough XML tool extraction (streaming + non-streaming)."""
import json
import pytest
from types import SimpleNamespace
from typing import AsyncGenerator

from llm.streaming import passthrough_xml_tool_extraction
from llm.converters import extract_xml_tools_from_passthrough_response


# ── SSE helpers ───────────────────────────────────────────────────────

def _sse_lines(*events: dict) -> list[str]:
    """Build a list of SSE line chunks from event dicts (mimics passthrough.stream_message)."""
    chunks = []
    for evt in events:
        evt_type = evt.get("type", "")
        chunks.append(f"event: {evt_type}\n")
        chunks.append(f"data: {json.dumps(evt)}\n")
    return chunks


def _make_stream(*events: dict) -> list[str]:
    return _sse_lines(*events)


def _msg_start() -> dict:
    return {"type": "message_start", "message": {"id": "msg_test", "model": "glm-4.7", "usage": {"input_tokens": 100}}}


def _cb_start_text(index: int = 0) -> dict:
    return {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}


def _cb_delta_text(text: str, index: int = 0) -> dict:
    return {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}


def _cb_stop(index: int = 0) -> dict:
    return {"type": "content_block_stop", "index": index}


def _msg_delta() -> dict:
    return {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 50}}


def _msg_stop() -> dict:
    return {"type": "message_stop"}


def _tool(name: str, command: str = "ls") -> SimpleNamespace:
    return SimpleNamespace(name=name, type="function")


def _request(tools=None) -> SimpleNamespace:
    if tools is None:
        tools = [_tool("Bash"), _tool("Read"), _tool("Write")]
    return SimpleNamespace(tools=tools)


async def _collect(stream_gen) -> list[str]:
    """Collect all chunks from an async generator into a list."""
    chunks = []
    async for chunk in stream_gen:
        chunks.append(chunk)
    return chunks


async def _make_async_gen(chunks: list[str]) -> AsyncGenerator[str, None]:
    for chunk in chunks:
        yield chunk


# ── ARGKV tool XML for GLM-4.7 ────────────────────────────────────────

ARGKV_BASH = (
    "<tool_call>Bash"
    "<arg_key>command</arg_key><arg_value>ls -la vendor/</arg_value>"
    "<arg_key>description</arg_key><arg_value>List vendor dir</arg_value>"
    "</tool_call>"
)

ARGKV_READ = (
    "<tool_call>Read"
    "<arg_key>file_path</arg_key><arg_value>/app/server.py</arg_value>"
    "</tool_call>"
)


# ── Streaming tests ───────────────────────────────────────────────────

class TestPassthroughXmlToolExtractionStreaming:

    @pytest.mark.asyncio
    async def test_no_tools_request_passes_through_unchanged(self):
        """Request with no tools defined — stream passes through byte-for-byte."""
        chunks = _make_stream(
            _msg_start(),
            _cb_start_text(),
            _cb_delta_text("Hello world"),
            _cb_stop(),
            _msg_delta(),
            _msg_stop(),
        )
        request = _request(tools=[])  # no tools
        result = await _collect(passthrough_xml_tool_extraction(_make_async_gen(chunks), request))
        assert result == chunks

    @pytest.mark.asyncio
    async def test_non_xml_stream_fast_path(self):
        """Stream without any <tool_call — all chunks pass through unchanged."""
        chunks = _make_stream(
            _msg_start(),
            _cb_start_text(),
            _cb_delta_text("This is plain text with no XML."),
            _cb_stop(),
            _msg_stop(),
        )
        request = _request()
        result = await _collect(passthrough_xml_tool_extraction(_make_async_gen(chunks), request))
        assert result == chunks

    @pytest.mark.asyncio
    async def test_argkv_single_tool_extracted_from_text_delta(self):
        """Complete argkv tool call in text_delta → emits proper tool_use SSE block."""
        chunks = _make_stream(
            _msg_start(),
            _cb_start_text(),
            _cb_delta_text(ARGKV_BASH),
            _cb_stop(),
            _msg_stop(),
        )
        request = _request()
        result = await _collect(passthrough_xml_tool_extraction(_make_async_gen(chunks), request))
        combined = "".join(result)

        # Must contain a tool_use content_block_start
        assert '"tool_use"' in combined
        assert '"Bash"' in combined
        # Must contain the command argument
        assert "ls -la vendor/" in combined
        # Must NOT contain raw <tool_call XML in the output
        assert "<tool_call>" not in combined

    @pytest.mark.asyncio
    async def test_text_before_tool_is_preserved(self):
        """Preamble text before <tool_call must be emitted as text_delta."""
        chunks = _make_stream(
            _msg_start(),
            _cb_start_text(),
            _cb_delta_text("Let me check: " + ARGKV_BASH),
            _cb_stop(),
            _msg_stop(),
        )
        request = _request()
        result = await _collect(passthrough_xml_tool_extraction(_make_async_gen(chunks), request))
        combined = "".join(result)

        # Preamble text must be in output
        assert "Let me check:" in combined
        # Tool must also be present
        assert '"tool_use"' in combined
        assert '"Bash"' in combined

    @pytest.mark.asyncio
    async def test_tool_xml_split_across_chunks(self):
        """<tool_call> XML split across multiple text_delta chunks is assembled correctly."""
        # Split the argkv XML at an arbitrary point in the middle
        split_at = len(ARGKV_BASH) // 2
        part1 = ARGKV_BASH[:split_at]
        part2 = ARGKV_BASH[split_at:]

        chunks = _make_stream(
            _msg_start(),
            _cb_start_text(),
            _cb_delta_text(part1),
            _cb_delta_text(part2),
            _cb_stop(),
            _msg_stop(),
        )
        request = _request()
        result = await _collect(passthrough_xml_tool_extraction(_make_async_gen(chunks), request))
        combined = "".join(result)

        assert '"tool_use"' in combined
        assert '"Bash"' in combined
        assert "ls -la vendor/" in combined

    @pytest.mark.asyncio
    async def test_incomplete_tool_at_stream_end_no_crash(self):
        """Truncated tool call XML at stream end — no exception raised."""
        # Tool XML without closing </tool_call>
        truncated = "<tool_call>Bash<arg_key>command</arg_key><arg_value>ls"

        chunks = _make_stream(
            _msg_start(),
            _cb_start_text(),
            _cb_delta_text(truncated),
            _cb_stop(),
            _msg_stop(),
        )
        request = _request()
        # Must not raise
        result = await _collect(passthrough_xml_tool_extraction(_make_async_gen(chunks), request))
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_orphan_event_line_not_emitted_when_data_suppressed(self):
        """When a text_delta with XML is transformed, the preceding event: line is suppressed."""
        chunks = _make_stream(
            _msg_start(),
            _cb_start_text(),
            _cb_delta_text(ARGKV_BASH),
            _cb_stop(),
            _msg_stop(),
        )
        request = _request()
        result = await _collect(passthrough_xml_tool_extraction(_make_async_gen(chunks), request))
        combined = "".join(result)

        # The "event: content_block_delta\n" line paired with the XML data: line
        # must NOT appear standalone — if it appeared, it'd be followed by the
        # raw <tool_call> data line, which we suppress. Verify no raw XML leaked.
        assert "arg_key" not in combined
        assert "arg_value" not in combined

    @pytest.mark.asyncio
    async def test_message_events_pass_through(self):
        """message_start, message_delta, message_stop always pass through."""
        chunks = _make_stream(
            _msg_start(),
            _cb_start_text(),
            _cb_delta_text(ARGKV_BASH),
            _cb_stop(),
            _msg_delta(),
            _msg_stop(),
        )
        request = _request()
        result = await _collect(passthrough_xml_tool_extraction(_make_async_gen(chunks), request))
        combined = "".join(result)

        assert '"message_start"' in combined
        assert '"message_delta"' in combined
        assert '"message_stop"' in combined


# ── Non-streaming tests ───────────────────────────────────────────────

class TestExtractXmlToolsFromPassthroughResponse:

    def _req(self):
        return _request()

    def test_non_stream_no_xml_passthrough_unchanged(self):
        """Response dict without any XML → returned unchanged (same object)."""
        response = {
            "id": "msg_1",
            "content": [{"type": "text", "text": "No tool calls here."}],
            "stop_reason": "end_turn",
        }
        result = extract_xml_tools_from_passthrough_response(response, self._req())
        assert result is response  # same object, no copy

    def test_non_stream_argkv_extracted_from_content(self):
        """Response with argkv tool in text block → tool_use block added, stop_reason updated."""
        text_with_tool = f"Analysis complete.\n{ARGKV_BASH}"
        response = {
            "id": "msg_1",
            "content": [{"type": "text", "text": text_with_tool}],
            "stop_reason": "end_turn",
        }
        result = extract_xml_tools_from_passthrough_response(response, self._req())

        assert result is not response  # new dict returned
        assert result["stop_reason"] == "tool_use"
        content = result["content"]
        tool_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0]["name"] == "Bash"
        assert tool_blocks[0]["input"].get("command") == "ls -la vendor/"

    def test_non_stream_multiple_tools_extracted(self):
        """Text block with 2 tool calls → 2 tool_use blocks in output."""
        text_with_tools = f"First call:\n{ARGKV_BASH}\nSecond call:\n{ARGKV_READ}"
        response = {
            "id": "msg_2",
            "content": [{"type": "text", "text": text_with_tools}],
            "stop_reason": "end_turn",
        }
        result = extract_xml_tools_from_passthrough_response(response, self._req())

        content = result["content"]
        tool_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        assert len(tool_blocks) == 2
        names = {b["name"] for b in tool_blocks}
        assert "Bash" in names
        assert "Read" in names

    def test_non_stream_no_tools_in_request_unchanged(self):
        """Request with no tools defined → response returned unchanged."""
        response = {
            "content": [{"type": "text", "text": f"XML here: {ARGKV_BASH}"}],
            "stop_reason": "end_turn",
        }
        req = _request(tools=[])
        result = extract_xml_tools_from_passthrough_response(response, req)
        assert result is response

    def test_non_stream_non_text_blocks_preserved(self):
        """Non-text content blocks (image, tool_result) are preserved unchanged."""
        response = {
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
                {"type": "text", "text": f"Done: {ARGKV_BASH}"},
            ],
            "stop_reason": "end_turn",
        }
        result = extract_xml_tools_from_passthrough_response(response, self._req())
        content = result["content"]
        tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        assert len(tool_results) == 1
