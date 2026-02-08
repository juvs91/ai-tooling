# tests/test_converters.py
"""Tests for Anthropic <-> OpenAI message conversion in llm/converters.py."""
import json
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.converters import (
    _convert_assistant_blocks,
    _convert_user_blocks,
    _convert_message_blocks,
    _tool_result_content_to_str,
    convert_anthropic_to_litellm,
    _convert_tool_cached,
    _tool_conversion_cache,
    clean_gemini_schema_cached,
    _gemini_schema_cache,
)
from llm.schemas import (
    MessagesRequest,
    ContentBlockText,
    ContentBlockToolUse,
    ContentBlockToolResult,
    ContentBlockThinking,
    ContentBlockRedactedThinking,
    ContentBlockServerToolUse,
    ContentBlockServerToolResult,
    Message,
)


# ── _tool_result_content_to_str ──────────────────────────────────────


class TestToolResultContentToStr:
    def test_none(self):
        assert _tool_result_content_to_str(None) == ""

    def test_string(self):
        assert _tool_result_content_to_str("hello") == "hello"

    def test_list_of_text_blocks(self):
        content = [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]
        assert _tool_result_content_to_str(content) == "line1\nline2"

    def test_list_with_image(self):
        content = [{"type": "text", "text": "data"}, {"type": "image", "source": {}}]
        result = _tool_result_content_to_str(content)
        assert "data" in result
        assert "[Image content]" in result

    def test_dict_text_block(self):
        assert _tool_result_content_to_str({"type": "text", "text": "val"}) == "val"

    def test_dict_non_text(self):
        result = _tool_result_content_to_str({"type": "other", "data": 123})
        assert "123" in result


# ── _convert_assistant_blocks ────────────────────────────────────────


class TestConvertAssistantBlocks:
    def test_text_only(self):
        blocks = [ContentBlockText(type="text", text="Hello world")]
        result = _convert_assistant_blocks(blocks)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Hello world"
        assert "tool_calls" not in result[0]

    def test_single_tool_use(self):
        blocks = [
            ContentBlockToolUse(
                type="tool_use", id="toolu_abc", name="Read",
                input={"file_path": "/tmp/test.py"},
            )
        ]
        result = _convert_assistant_blocks(blocks)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert len(msg["tool_calls"]) == 1
        tc = msg["tool_calls"][0]
        assert tc["id"] == "toolu_abc"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "Read"
        # arguments must be a JSON string, not a dict
        assert isinstance(tc["function"]["arguments"], str)
        parsed = json.loads(tc["function"]["arguments"])
        assert parsed["file_path"] == "/tmp/test.py"

    def test_multiple_tool_uses(self):
        blocks = [
            ContentBlockToolUse(
                type="tool_use", id="toolu_1", name="Read", input={"path": "a"},
            ),
            ContentBlockToolUse(
                type="tool_use", id="toolu_2", name="Grep", input={"pattern": "foo"},
            ),
        ]
        result = _convert_assistant_blocks(blocks)
        assert len(result) == 1
        assert len(result[0]["tool_calls"]) == 2
        assert result[0]["tool_calls"][0]["function"]["name"] == "Read"
        assert result[0]["tool_calls"][1]["function"]["name"] == "Grep"

    def test_mixed_text_and_tool_use(self):
        blocks = [
            ContentBlockText(type="text", text="I'll read that file."),
            ContentBlockToolUse(
                type="tool_use", id="toolu_abc", name="Read",
                input={"file_path": "/foo"},
            ),
        ]
        result = _convert_assistant_blocks(blocks)
        assert len(result) == 1
        msg = result[0]
        assert msg["content"] == "I'll read that file."
        assert len(msg["tool_calls"]) == 1

    def test_thinking_blocks_stripped(self):
        blocks = [
            ContentBlockThinking(type="thinking", thinking="hmm...", signature="sig"),
            ContentBlockText(type="text", text="Answer"),
        ]
        result = _convert_assistant_blocks(blocks)
        assert len(result) == 1
        assert result[0]["content"] == "Answer"
        assert "tool_calls" not in result[0]

    def test_redacted_thinking_stripped(self):
        blocks = [
            ContentBlockRedactedThinking(type="redacted_thinking", data="xxx"),
            ContentBlockText(type="text", text="Result"),
        ]
        result = _convert_assistant_blocks(blocks)
        assert result[0]["content"] == "Result"

    def test_server_tool_use_flattened_to_text(self):
        blocks = [
            ContentBlockServerToolUse(
                type="server_tool_use", id="srv_1", name="web_search",
                input={"query": "test"},
            ),
        ]
        result = _convert_assistant_blocks(blocks)
        assert len(result) == 1
        assert "ServerTool" in result[0]["content"]
        assert "tool_calls" not in result[0]

    def test_empty_blocks(self):
        result = _convert_assistant_blocks([])
        assert len(result) == 1
        assert result[0]["content"] == "..."


# ── _convert_user_blocks ─────────────────────────────────────────────


class TestConvertUserBlocks:
    def test_text_only(self):
        blocks = [ContentBlockText(type="text", text="Hello")]
        result = _convert_user_blocks(blocks)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_single_tool_result(self):
        blocks = [
            ContentBlockToolResult(
                type="tool_result", tool_use_id="toolu_abc",
                content="file contents here",
            ),
        ]
        result = _convert_user_blocks(blocks)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "toolu_abc"
        assert msg["content"] == "file contents here"

    def test_multiple_tool_results(self):
        blocks = [
            ContentBlockToolResult(
                type="tool_result", tool_use_id="toolu_1", content="result1",
            ),
            ContentBlockToolResult(
                type="tool_result", tool_use_id="toolu_2", content="result2",
            ),
        ]
        result = _convert_user_blocks(blocks)
        assert len(result) == 2
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "toolu_1"
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "toolu_2"

    def test_mixed_text_and_tool_result(self):
        blocks = [
            ContentBlockToolResult(
                type="tool_result", tool_use_id="toolu_abc", content="data",
            ),
            ContentBlockText(type="text", text="Based on that, continue"),
        ]
        result = _convert_user_blocks(blocks)
        assert len(result) == 2
        # tool messages come first
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "toolu_abc"
        # then user text
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "Based on that, continue"

    def test_tool_result_with_nested_list_content(self):
        blocks = [
            ContentBlockToolResult(
                type="tool_result", tool_use_id="toolu_abc",
                content=[{"type": "text", "text": "nested content"}],
            ),
        ]
        result = _convert_user_blocks(blocks)
        assert result[0]["content"] == "nested content"

    def test_tool_result_with_is_error(self):
        blocks = [
            ContentBlockToolResult(
                type="tool_result", tool_use_id="toolu_abc",
                content="file not found",
            ),
        ]
        # Manually set is_error since the model doesn't have it as a field
        blocks[0].__dict__["is_error"] = True
        result = _convert_user_blocks(blocks)
        assert result[0]["content"].startswith("[ERROR]")

    def test_server_tool_result_flattened(self):
        blocks = [
            ContentBlockServerToolResult(
                type="server_tool_result", tool_use_id="srv_1",
                content="search results",
            ),
        ]
        result = _convert_user_blocks(blocks)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "ServerTool Result" in result[0]["content"]

    def test_empty_blocks(self):
        result = _convert_user_blocks([])
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "..."


# ── _convert_message_blocks ──────────────────────────────────────────


class TestConvertMessageBlocks:
    def test_string_content_passthrough(self):
        msg = Message(role="user", content="hello")
        result = _convert_message_blocks(msg)
        assert result == [{"role": "user", "content": "hello"}]

    def test_empty_string_content(self):
        msg = Message(role="user", content="   ")
        result = _convert_message_blocks(msg)
        assert result == [{"role": "user", "content": "..."}]

    def test_assistant_with_tool_use(self):
        msg = Message(role="assistant", content=[
            ContentBlockText(type="text", text="Reading file"),
            ContentBlockToolUse(
                type="tool_use", id="toolu_1", name="Read",
                input={"file_path": "/test"},
            ),
        ])
        result = _convert_message_blocks(msg)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Reading file"
        assert len(result[0]["tool_calls"]) == 1

    def test_user_with_tool_result(self):
        msg = Message(role="user", content=[
            ContentBlockToolResult(
                type="tool_result", tool_use_id="toolu_1",
                content="file data",
            ),
        ])
        result = _convert_message_blocks(msg)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "toolu_1"


# ── convert_anthropic_to_litellm (integration) ──────────────────────


class TestConvertAnthropicToLitellm:
    def test_agentic_loop_round_trip(self):
        """Full agentic conversation with tool use and tool result."""
        request = MessagesRequest(
            model="openai/glm-4.7",
            max_tokens=4096,
            messages=[
                Message(role="user", content="Read /tmp/test.py"),
                Message(role="assistant", content=[
                    ContentBlockText(type="text", text="I'll read that file."),
                    ContentBlockToolUse(
                        type="tool_use", id="toolu_abc123",
                        name="Read", input={"file_path": "/tmp/test.py"},
                    ),
                ]),
                Message(role="user", content=[
                    ContentBlockToolResult(
                        type="tool_result", tool_use_id="toolu_abc123",
                        content="print('hello world')",
                    ),
                ]),
                Message(role="assistant", content="The file contains a hello world script."),
            ],
            system="You are a coding assistant.",
        )

        result = convert_anthropic_to_litellm(request)
        msgs = result["messages"]

        # system + 4 conversation messages (tool_result expands to role:tool)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "Read /tmp/test.py"

        # assistant with tool_calls
        assert msgs[2]["role"] == "assistant"
        assert msgs[2]["content"] == "I'll read that file."
        assert len(msgs[2]["tool_calls"]) == 1
        tc = msgs[2]["tool_calls"][0]
        assert tc["id"] == "toolu_abc123"
        assert tc["function"]["name"] == "Read"
        assert isinstance(tc["function"]["arguments"], str)

        # tool result as role:tool
        assert msgs[3]["role"] == "tool"
        assert msgs[3]["tool_call_id"] == "toolu_abc123"
        assert msgs[3]["content"] == "print('hello world')"

        # final assistant text
        assert msgs[4]["role"] == "assistant"
        assert msgs[4]["content"] == "The file contains a hello world script."

    def test_tool_choice_any_maps_to_required(self):
        request = MessagesRequest(
            model="openai/glm-4.7",
            max_tokens=1024,
            messages=[Message(role="user", content="test")],
            tool_choice={"type": "any"},
        )
        result = convert_anthropic_to_litellm(request)
        assert result["tool_choice"] == "required"

    def test_tool_choice_auto_stays_auto(self):
        request = MessagesRequest(
            model="openai/glm-4.7",
            max_tokens=1024,
            messages=[Message(role="user", content="test")],
            tool_choice={"type": "auto"},
        )
        result = convert_anthropic_to_litellm(request)
        assert result["tool_choice"] == "auto"

    def test_tool_choice_specific_tool(self):
        request = MessagesRequest(
            model="openai/glm-4.7",
            max_tokens=1024,
            messages=[Message(role="user", content="test")],
            tool_choice={"type": "tool", "name": "Read"},
        )
        result = convert_anthropic_to_litellm(request)
        assert result["tool_choice"] == {
            "type": "function",
            "function": {"name": "Read"},
        }

    def test_multi_tool_result_expansion(self):
        """A single user message with 2 tool_results becomes 2 tool messages."""
        request = MessagesRequest(
            model="openai/glm-4.7",
            max_tokens=1024,
            messages=[
                Message(role="assistant", content=[
                    ContentBlockToolUse(
                        type="tool_use", id="toolu_1", name="Read",
                        input={"file_path": "a.py"},
                    ),
                    ContentBlockToolUse(
                        type="tool_use", id="toolu_2", name="Read",
                        input={"file_path": "b.py"},
                    ),
                ]),
                Message(role="user", content=[
                    ContentBlockToolResult(
                        type="tool_result", tool_use_id="toolu_1",
                        content="content_a",
                    ),
                    ContentBlockToolResult(
                        type="tool_result", tool_use_id="toolu_2",
                        content="content_b",
                    ),
                ]),
            ],
        )
        result = convert_anthropic_to_litellm(request)
        msgs = result["messages"]

        # assistant with 2 tool_calls
        assert len(msgs[0]["tool_calls"]) == 2

        # 2 separate tool messages
        assert msgs[1]["role"] == "tool"
        assert msgs[1]["tool_call_id"] == "toolu_1"
        assert msgs[2]["role"] == "tool"
        assert msgs[2]["tool_call_id"] == "toolu_2"


# ── JSON Repair in streaming (_compute_repair_suffix) ────────────────

from llm.streaming import _compute_repair_suffix


class TestComputeRepairSuffix:
    """Tests for the streaming JSON repair helper."""

    def test_valid_json_returns_none(self):
        assert _compute_repair_suffix('{"path": "/tmp/test.py"}', 1) is None

    def test_empty_string_returns_none(self):
        assert _compute_repair_suffix("", 1) is None

    def test_missing_closing_brace_returns_suffix(self):
        result = _compute_repair_suffix('{"path": "/tmp/test.py"', 1)
        assert result is not None
        assert "}" in result

    def test_repair_suffix_produces_valid_json(self):
        truncated = '{"path": "/tmp/test.py"'
        suffix = _compute_repair_suffix(truncated, 1)
        assert suffix is not None
        full = truncated + suffix
        parsed = json.loads(full)
        assert parsed["path"] == "/tmp/test.py"

    def test_totally_invalid_returns_none(self):
        assert _compute_repair_suffix("not json at all <<<>>>", 1) is None

    def test_missing_value_quote(self):
        # e.g. {"command": "ls -la} — missing closing quote + brace
        truncated = '{"command": "ls -la'
        suffix = _compute_repair_suffix(truncated, 1)
        if suffix:
            full = truncated + suffix
            parsed = json.loads(full)
            assert "ls -la" in parsed["command"]


# ── Tool Conversion Cache ─────────────────────────────────────────────


class TestConvertToolCached:
    """Tests for _convert_tool_cached memoization."""

    def setup_method(self):
        _tool_conversion_cache.clear()

    def test_returns_correct_format(self):
        tool = {"name": "Read", "description": "Read files", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}
        result = _convert_tool_cached(tool, is_gemini=False)
        assert result["type"] == "function"
        assert result["function"]["name"] == "Read"
        assert result["function"]["description"] == "Read files"
        assert result["function"]["parameters"]["type"] == "object"

    def test_cache_hit_returns_same_object(self):
        tool = {"name": "Read", "description": "Read", "input_schema": {"type": "object"}}
        r1 = _convert_tool_cached(tool, is_gemini=False)
        r2 = _convert_tool_cached(tool, is_gemini=False)
        assert r1 is r2  # same object from cache

    def test_different_schema_different_cache_entry(self):
        tool_a = {"name": "Read", "description": "Read", "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}}}
        tool_b = {"name": "Read", "description": "Read", "input_schema": {"type": "object", "properties": {"b": {"type": "integer"}}}}
        r1 = _convert_tool_cached(tool_a, is_gemini=False)
        r2 = _convert_tool_cached(tool_b, is_gemini=False)
        assert r1 is not r2

    def test_gemini_flag_creates_separate_entry(self):
        tool = {"name": "Read", "description": "Read", "input_schema": {"type": "object"}}
        r1 = _convert_tool_cached(tool, is_gemini=False)
        r2 = _convert_tool_cached(tool, is_gemini=True)
        assert r1 is not r2

    def test_populates_cache(self):
        tool = {"name": "TestTool", "description": "test", "input_schema": {"type": "object"}}
        _convert_tool_cached(tool, is_gemini=False)
        assert len(_tool_conversion_cache) == 1


# ── Gemini Schema Memoization ─────────────────────────────────────────


class TestCleanGeminiSchemaCached:
    """Tests for clean_gemini_schema_cached memoization."""

    def setup_method(self):
        _gemini_schema_cache.clear()

    def test_returns_cleaned_schema(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}
        result = clean_gemini_schema_cached(schema)
        assert "additionalProperties" not in result
        assert result["properties"]["a"]["type"] == "string"

    def test_cache_hit_returns_same_object(self):
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        r1 = clean_gemini_schema_cached(schema)
        r2 = clean_gemini_schema_cached(schema)
        assert r1 is r2

    def test_different_schemas_cached_separately(self):
        s1 = {"type": "object", "properties": {"a": {"type": "string"}}}
        s2 = {"type": "object", "properties": {"b": {"type": "integer"}}}
        r1 = clean_gemini_schema_cached(s1)
        r2 = clean_gemini_schema_cached(s2)
        assert r1 is not r2
        assert len(_gemini_schema_cache) == 2
