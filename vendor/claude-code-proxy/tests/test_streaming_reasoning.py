# tests/test_streaming_reasoning.py
"""Tests for DeepSeek-reasoner reasoning_content tool call detection.

Validates that <tool_call> XML in reasoning_content is correctly detected
and emitted as tool_use blocks when no_tools_mode=True.
Includes exhaustive parametrized tests for all 15 CC tool types.
"""
import json
import pytest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.streaming import handle_streaming


# ── Tool definitions for all CC tool types ────────────────────────────

CC_TOOLS = [
    {"name": "Read", "input": {"file_path": "/test.py"}},
    {"name": "Bash", "input": {"command": "ls -la /tmp"}},
    {"name": "Edit", "input": {"file_path": "/test.py", "old_string": "foo", "new_string": "bar"}},
    {"name": "Write", "input": {"file_path": "/test.py", "content": "print('hello')"}},
    {"name": "Glob", "input": {"pattern": "**/*.py"}},
    {"name": "Grep", "input": {"pattern": "def main"}},
    {"name": "TodoWrite", "input": {"todos": [{"content": "Fix bug", "status": "pending", "activeForm": "Fixing bug"}]}},
    {"name": "Task", "input": {"description": "Search code", "prompt": "Find all utils", "subagent_type": "Explore"}},
    {"name": "WebSearch", "input": {"query": "python asyncio tutorial"}},
    {"name": "WebFetch", "input": {"url": "https://example.com", "prompt": "Extract title"}},
    {"name": "AskUserQuestion", "input": {"questions": [{"question": "Which approach?", "header": "Approach", "options": [{"label": "A", "description": "Option A"}, {"label": "B", "description": "Option B"}], "multiSelect": False}]}},
    {"name": "EnterPlanMode", "input": {}},
    {"name": "ExitPlanMode", "input": {}},
    {"name": "NotebookEdit", "input": {"notebook_path": "/test.ipynb", "new_source": "print(1)"}},
    {"name": "Skill", "input": {"skill": "commit"}},
]

# Build tool schemas for mock requests (all tools available)
_TOOL_SCHEMAS = [
    SimpleNamespace(
        name=t["name"],
        description=f"{t['name']} tool",
        input_schema={"type": "object", "properties": {k: {"type": "string"} for k in t["input"]}} if t["input"] else {"type": "object", "properties": {}},
    )
    for t in CC_TOOLS
]


def _tool_xml(name: str, input_dict: dict) -> str:
    """Build <tool_call> XML string for a given tool name and input."""
    input_json = json.dumps(input_dict)
    return f'<tool_call name="{name}"><input>{input_json}</input></tool_call>'


# ── Helpers ──────────────────────────────────────────────────────────

def _make_tool():
    """Build a mock Anthropic tool definition."""
    return SimpleNamespace(
        name="Read",
        description="Read a file",
        input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
    )


def _make_request(model="openai/deepseek-reasoner", tools=None):
    """Build a mock request with tools."""
    return SimpleNamespace(
        model=model,
        original_model=model,
        tools=tools or _TOOL_SCHEMAS,
    )


def _chunk(reasoning_content=None, content=None, finish_reason=None, tool_calls=None):
    """Build a mock LiteLLM streaming chunk."""
    delta = {}
    if reasoning_content is not None:
        delta["reasoning_content"] = reasoning_content
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls

    choice = SimpleNamespace(
        delta=delta,
        finish_reason=finish_reason,
    )
    return SimpleNamespace(choices=[choice], usage=None)


async def _async_gen(chunks):
    """Convert list of chunks to async generator."""
    for c in chunks:
        yield c


async def _collect_events(request, chunks):
    """Run handle_streaming and collect all SSE events."""
    events = []
    async for event in handle_streaming(
        _async_gen(chunks),
        request,
        model_context_window=0,
    ):
        events.append(event)
    return events


def _find_tool_use_events(events):
    """Extract tool_use content_block_start events from SSE output."""
    tool_events = []
    for ev in events:
        if "content_block_start" in ev and "tool_use" in ev:
            tool_events.append(json.loads(ev.split("data: ")[1].strip()))
    return tool_events


def _find_text_deltas(events):
    """Extract all text_delta content from SSE events."""
    texts = []
    for ev in events:
        if "text_delta" in ev:
            data = json.loads(ev.split("data: ")[1].strip())
            texts.append(data.get("delta", {}).get("text", ""))
    return texts


def _find_stop_reason(events):
    """Extract stop_reason from message_delta event."""
    for ev in events:
        if "message_delta" in ev:
            data = json.loads(ev.split("data: ")[1].strip())
            return data.get("delta", {}).get("stop_reason")
    return None


# ── Tests ────────────────────────────────────────────────────────────

class TestDeepSeekReasoningToolDetection:
    """Test that <tool_call> XML in reasoning_content is detected for no_tools_mode."""

    @pytest.mark.asyncio
    async def test_reasoning_tool_call_detected(self):
        """Tool call in reasoning_content is emitted as tool_use block."""
        tool_xml = '<tool_call name="Read"><input>{"file_path": "/test.py"}</input></tool_call>'
        chunks = [
            _chunk(reasoning_content="Let me read the file. "),
            _chunk(reasoning_content=tool_xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"Expected tool_use event, got events: {events}"
        assert tool_events[0]["content_block"]["name"] == "Read"

        stop = _find_stop_reason(events)
        assert stop == "tool_use"

    @pytest.mark.asyncio
    async def test_reasoning_tool_split_across_chunks(self):
        """Tool call split across reasoning chunks is assembled by XmlToolBuffer."""
        chunks = [
            _chunk(reasoning_content='I will read the file. <tool_call name="Re'),
            _chunk(reasoning_content='ad"><input>{"file_path": "/test.py"}</input></tool_call>'),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"Expected tool_use from split chunks, got: {events}"
        assert tool_events[0]["content_block"]["name"] == "Read"

    @pytest.mark.asyncio
    async def test_reasoning_no_tool_emits_text(self):
        """Reasoning without tool_call is emitted as text at stream end."""
        chunks = [
            _chunk(reasoning_content="I need to think about this problem carefully."),
            _chunk(content="Here is my answer."),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        texts = _find_text_deltas(events)
        combined = "".join(texts)
        # Reasoning text should be emitted (either inline or at end)
        assert "think about this" in combined or "Here is my answer" in combined

        # No tool_use events
        tool_events = _find_tool_use_events(events)
        assert len(tool_events) == 0

    @pytest.mark.asyncio
    async def test_reasoning_tool_suppresses_reasoning_text(self):
        """After emitting tool from reasoning, reasoning text is suppressed."""
        tool_xml = '<tool_call name="Read"><input>{"file_path": "/test.py"}</input></tool_call>'
        chunks = [
            _chunk(reasoning_content="Long reasoning text about what to do. " * 20),
            _chunk(reasoning_content=tool_xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        # Tool should be emitted
        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1

        # Reasoning text before the tool should NOT be in text_delta events
        # (because has_any_tools guard in _process_reasoning_buffer suppresses it)
        texts = _find_text_deltas(events)
        combined = "".join(texts)
        assert "Long reasoning text" not in combined


class TestSafetyNetReasoningBuffer:
    """Test that safety net checks reasoning_buffer too."""

    @pytest.mark.asyncio
    async def test_safety_net_catches_reasoning_buffer(self):
        """If XmlToolBuffer somehow misses a tool call in reasoning, safety net catches it."""
        # Use a tool call format that might not be caught by XmlToolBuffer
        # but will be caught by extract_tool_calls_from_text
        tool_xml = '<tool_call name="Read"><input>{"file_path": "/test.py"}</input></tool_call>'
        chunks = [
            _chunk(reasoning_content=tool_xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        # Should have tool_use events (from XmlToolBuffer or safety net)
        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1
        assert tool_events[0]["content_block"]["name"] == "Read"

        stop = _find_stop_reason(events)
        assert stop == "tool_use"


class TestNonStreamingReasoningExtraction:
    """Test non-streaming path extracts tools from reasoning_text."""

    def test_reasoning_text_tool_extraction(self):
        """Tool call in reasoning_text is extracted in non-streaming path."""
        from llm.converters import convert_litellm_to_anthropic

        tool_xml = '<tool_call name="Read"><input>{"file_path": "/test.py"}</input></tool_call>'
        response = {
            "id": "test-123",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": f"Let me read the file. {tool_xml}",
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 50},
            "model": "deepseek-reasoner",
        }
        request = _make_request(model="openai/deepseek-reasoner")

        with patch("llm.converters.is_no_tools_model", return_value=True):
            result = convert_litellm_to_anthropic(response, request)

        # Result is a MessagesResponse pydantic model
        content = result.content
        tool_blocks = [b for b in content if b.type == "tool_use"]
        assert len(tool_blocks) >= 1, f"Expected tool_use in content, got: {content}"
        assert tool_blocks[0].name == "Read"

        # stop_reason should be tool_use
        assert result.stop_reason == "tool_use"


# ── Parametrized: all 15 CC tool types ──────────────────────────────

_TOOL_IDS = [t["name"] for t in CC_TOOLS]


class TestAllToolTypesInReasoning:
    """Every CC tool type detected in reasoning_content (streaming, no_tools_mode)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_def", CC_TOOLS, ids=_TOOL_IDS)
    async def test_tool_in_reasoning_content(self, tool_def):
        """Tool call in reasoning_content → tool_use SSE, no raw XML in text."""
        xml = _tool_xml(tool_def["name"], tool_def["input"])
        chunks = [
            _chunk(reasoning_content="Thinking about this. "),
            _chunk(reasoning_content=xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"No tool_use for {tool_def['name']}: {events}"
        assert tool_events[0]["content_block"]["name"] == tool_def["name"]

        stop = _find_stop_reason(events)
        assert stop == "tool_use", f"Expected tool_use stop, got {stop}"

        # No raw XML leaked into text
        texts = _find_text_deltas(events)
        combined = "".join(texts)
        assert "<tool_call" not in combined, f"Raw XML in text: {combined}"


class TestAllToolTypesInContent:
    """Every CC tool type detected in content (streaming, no_tools_mode)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_def", CC_TOOLS, ids=_TOOL_IDS)
    async def test_tool_in_content(self, tool_def):
        """Tool call in content → tool_use SSE, no raw XML in text."""
        xml = _tool_xml(tool_def["name"], tool_def["input"])
        chunks = [
            _chunk(content="Here is the result. "),
            _chunk(content=xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"No tool_use for {tool_def['name']}: {events}"
        assert tool_events[0]["content_block"]["name"] == tool_def["name"]

        stop = _find_stop_reason(events)
        assert stop == "tool_use", f"Expected tool_use stop, got {stop}"

        texts = _find_text_deltas(events)
        combined = "".join(texts)
        assert "<tool_call" not in combined, f"Raw XML in text: {combined}"


class TestAllToolTypesNonStreaming:
    """Every CC tool type detected in reasoning_text (non-streaming path)."""

    @pytest.mark.parametrize("tool_def", CC_TOOLS, ids=_TOOL_IDS)
    def test_tool_in_reasoning_nonstreaming(self, tool_def):
        """Tool in reasoning_text extracted in non-streaming convert."""
        from llm.converters import convert_litellm_to_anthropic

        xml = _tool_xml(tool_def["name"], tool_def["input"])
        response = {
            "id": "test-123",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": f"Let me do this. {xml}",
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 50},
            "model": "deepseek-reasoner",
        }
        request = _make_request(model="openai/deepseek-reasoner")

        with patch("llm.converters.is_no_tools_model", return_value=True):
            result = convert_litellm_to_anthropic(response, request)

        tool_blocks = [b for b in result.content if b.type == "tool_use"]
        assert len(tool_blocks) >= 1, f"No tool_use for {tool_def['name']}: {result.content}"
        assert tool_blocks[0].name == tool_def["name"]
        assert result.stop_reason == "tool_use"


# ── Edge case tests ─────────────────────────────────────────────────

class TestToolEdgeCases:
    """Edge cases from real DeepSeek sessions and adversarial inputs."""

    @pytest.mark.asyncio
    async def test_tool_with_system_reminder_in_reasoning(self):
        """<system-reminder> inside <tool_call> (real DeepSeek failure case)."""
        xml = (
            '<tool_call name="Read">'
            '<system-reminder>Note: file was read before</system-reminder>'
            '<input>{"file_path": "/test.py"}</input>'
            '</tool_call>'
        )
        chunks = [
            _chunk(reasoning_content="I need to read. "),
            _chunk(reasoning_content=xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"system-reminder broke detection: {events}"
        assert tool_events[0]["content_block"]["name"] == "Read"

        texts = _find_text_deltas(events)
        combined = "".join(texts)
        assert "<tool_call" not in combined

    @pytest.mark.asyncio
    async def test_tool_with_nested_xml_in_input(self):
        """Input JSON contains XML characters (escaped in JSON string)."""
        input_dict = {"file_path": "/test.html", "content": "<div>hello &amp; world</div>"}
        xml = _tool_xml("Write", input_dict)
        chunks = [
            _chunk(reasoning_content=xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"Nested XML broke detection: {events}"
        assert tool_events[0]["content_block"]["name"] == "Write"

    @pytest.mark.asyncio
    async def test_tool_with_very_long_input(self):
        """Write tool with 50K+ character content."""
        long_content = "x" * 50_000
        input_dict = {"file_path": "/big.txt", "content": long_content}
        xml = _tool_xml("Write", input_dict)
        chunks = [
            _chunk(reasoning_content=xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"Long input broke detection: {events}"
        assert tool_events[0]["content_block"]["name"] == "Write"

    @pytest.mark.asyncio
    async def test_tool_with_empty_input(self):
        """Tool with no input (e.g., EnterPlanMode)."""
        xml = '<tool_call name="EnterPlanMode"><input>{}</input></tool_call>'
        chunks = [
            _chunk(reasoning_content="Entering plan mode. "),
            _chunk(reasoning_content=xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"Empty input broke detection: {events}"
        assert tool_events[0]["content_block"]["name"] == "EnterPlanMode"

    @pytest.mark.asyncio
    async def test_tool_mixed_reasoning_and_content(self):
        """Tool in reasoning + normal text in content (same stream)."""
        tool_xml = _tool_xml("Read", {"file_path": "/test.py"})
        chunks = [
            _chunk(reasoning_content="Thinking. "),
            _chunk(reasoning_content=tool_xml),
            _chunk(content="Here is some text after reasoning."),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"Mixed mode broke detection: {events}"
        assert tool_events[0]["content_block"]["name"] == "Read"

        stop = _find_stop_reason(events)
        assert stop == "tool_use"

    @pytest.mark.asyncio
    async def test_multiple_different_tools_in_sequence(self):
        """Read → Edit → Bash in reasoning_content sequentially."""
        xml1 = _tool_xml("Read", {"file_path": "/test.py"})
        xml2 = _tool_xml("Edit", {"file_path": "/test.py", "old_string": "foo", "new_string": "bar"})
        xml3 = _tool_xml("Bash", {"command": "python /test.py"})
        chunks = [
            _chunk(reasoning_content="Step 1. "),
            _chunk(reasoning_content=xml1),
            _chunk(reasoning_content="Step 2. "),
            _chunk(reasoning_content=xml2),
            _chunk(reasoning_content="Step 3. "),
            _chunk(reasoning_content=xml3),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        names = [te["content_block"]["name"] for te in tool_events]
        assert "Read" in names, f"Read missing: {names}"
        assert "Edit" in names, f"Edit missing: {names}"
        assert "Bash" in names, f"Bash missing: {names}"

        stop = _find_stop_reason(events)
        assert stop == "tool_use"

    @pytest.mark.asyncio
    async def test_tool_call_with_reasoning_tags_inside(self):
        """<reasoning> tags inside <tool_call> are stripped by _REASONING_SKIP."""
        xml = (
            '<tool_call name="Read">'
            '<reasoning>I need to read this file to understand the code.</reasoning>'
            '<input>{"file_path": "/test.py"}</input>'
            '</tool_call>'
        )
        chunks = [
            _chunk(reasoning_content=xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"Reasoning tags broke detection: {events}"
        assert tool_events[0]["content_block"]["name"] == "Read"

    @pytest.mark.asyncio
    async def test_tool_in_content_not_reasoning(self):
        """Tool in content field (not reasoning) still detected for no_tools model."""
        xml = _tool_xml("Bash", {"command": "echo hello"})
        chunks = [
            _chunk(content=xml),
            _chunk(finish_reason="stop"),
        ]
        request = _make_request()

        with patch("llm.transformers.stream_event.is_no_tools_model", return_value=True):
            events = await _collect_events(request, chunks)

        tool_events = _find_tool_use_events(events)
        assert len(tool_events) >= 1, f"Content tool not detected: {events}"
        assert tool_events[0]["content_block"]["name"] == "Bash"

        texts = _find_text_deltas(events)
        combined = "".join(texts)
        assert "<tool_call" not in combined
