# tests/test_tool_regex.py
"""Exhaustive tests for the 5 tool call regex patterns + extraction + XmlToolBuffer.

Covers:
  1. _TOOL_CALL_RE — standard format with known inner tags + _REASONING_SKIP
  2. _TOOL_CALL_FALLBACK_RE — any inner tag pair with backreference
  3. _TOOL_CALL_BARE_RE — no inner tags (bare JSON)
  4. _TOOL_CALL_ARGKV_RE — GLM <arg_key>/<arg_value> format
  5. _TOOL_DILUTED_RE — <tool_name>/<args> format (post-prompt-dilution)
  Plus: single-quote name=, extract_tool_calls_from_text, XmlToolBuffer
"""
import json
import pytest
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.tool_prompting import (
    _TOOL_CALL_RE,
    _TOOL_CALL_FALLBACK_RE,
    _TOOL_CALL_BARE_RE,
    _TOOL_CALL_ARGKV_RE,
    _TOOL_DILUTED_RE,
    _ARG_KV_PAIR_RE,
    _REAL_NAME_RE,
    _XML_PARAM_TAG_RE,
    _XML_ATTR_PARAM_RE,
    _normalize_escaped_xml,
    _strip_inner_xml_tags,
    _parse_xml_as_tags,
    _safe_parse_tool_input,
    _repair_tool_input,
    _greedy_extract_json_fields,
    _schema_aware_cleanup,
    _get_tool_schema,
    _get_tool_properties,
    extract_tool_calls_from_text,
    strip_tool_call_xml,
    recover_truncated_deterministic,
    build_tool_prompt,
    _build_few_shot_examples,
    XmlToolBuffer,
    _build_valid_tool_names,
    validate_tool_name,
)

# ── Shared tool definitions for tests ──────────────────────────────────

TOOLS = [
    {"name": "Read", "description": "Read file", "input_schema": {
        "type": "object",
        "properties": {"file_path": {"type": "string"}},
        "required": ["file_path"],
    }},
    {"name": "Bash", "description": "Run command", "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }},
    {"name": "Write", "description": "Write file", "input_schema": {
        "type": "object",
        "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["file_path", "content"],
    }},
    {"name": "Edit", "description": "Edit file", "input_schema": {
        "type": "object",
        "properties": {"file_path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}},
        "required": ["file_path", "old_string", "new_string"],
    }},
    {"name": "Grep", "description": "Search code", "input_schema": {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
        "required": ["pattern"],
    }},
    {"name": "TodoWrite", "description": "Write todos", "input_schema": {
        "type": "object",
        "properties": {"todos": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                "activeForm": {"type": "string"},
            },
            "required": ["content", "status", "activeForm"],
        }}},
        "required": ["todos"],
    }},
    {"name": "EnterPlanMode", "description": "Enter plan mode", "input_schema": {
        "type": "object", "properties": {},
    }},
]

VALID_NAMES = {t["name"] for t in TOOLS}


# ═══════════════════════════════════════════════════════════════════════
# 1. _TOOL_CALL_RE — Primary regex with known inner tags
# ═══════════════════════════════════════════════════════════════════════

class TestToolCallRE:
    """Primary regex: <tool_call name="X"><input>{json}</input></tool_call>"""

    def test_standard_input_tag(self):
        xml = '<tool_call name="Read"><input>{"file_path": "/test.py"}</input></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"
        parsed = json.loads(m.group(2))
        assert parsed["file_path"] == "/test.py"

    def test_textarea_tag(self):
        xml = '<tool_call name="Write"><textarea>{"file_path": "/x.py", "content": "hello"}</textarea></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Write"

    def test_arguments_tag(self):
        xml = '<tool_call name="Bash"><arguments>{"command": "ls"}</arguments></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Bash"

    def test_params_tag(self):
        xml = '<tool_call name="Grep"><params>{"pattern": "TODO"}</params></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Grep"

    def test_json_tag(self):
        xml = '<tool_call name="Read"><json>{"file_path": "/a.py"}</json></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"

    def test_content_tag(self):
        xml = '<tool_call name="Read"><content>{"file_path": "/a.py"}</content></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None

    def test_parameters_tag(self):
        xml = '<tool_call name="Read"><parameters>{"file_path": "/a.py"}</parameters></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None

    def test_single_quotes_name_attr(self):
        """DeepSeek-reasoner uses single quotes for name= attribute."""
        xml = """<tool_call name='Read'><input>{"file_path": "/test.py"}</input></tool_call>"""
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"

    def test_reasoning_tags_inside_tool_call(self):
        """<reasoning> tags inside <tool_call> are skipped by _REASONING_SKIP."""
        xml = (
            '<tool_call name="Read">'
            '<reasoning>I need to read this file to understand the structure.</reasoning>'
            '<input>{"file_path": "/test.py"}</input>'
            '</tool_call>'
        )
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"
        parsed = json.loads(m.group(2))
        assert parsed["file_path"] == "/test.py"

    def test_multiple_reasoning_tags(self):
        """Multiple <reasoning> blocks before input."""
        xml = (
            '<tool_call name="Read">'
            '<reasoning>First thought.</reasoning>'
            '<reasoning>Second thought.</reasoning>'
            '<input>{"file_path": "/test.py"}</input>'
            '</tool_call>'
        )
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"

    def test_whitespace_variations(self):
        xml = '<tool_call  name="Read" >\n  <input>\n{"file_path": "/a.py"}\n  </input>\n</tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"

    def test_unicode_in_input(self):
        xml = '<tool_call name="Write"><input>{"file_path": "/日本語.py", "content": "café"}</input></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        parsed = json.loads(m.group(2))
        assert "日本語" in parsed["file_path"]

    def test_complex_json_todowrite(self):
        """TodoWrite with nested array (real CC format)."""
        todos = [
            {"content": "Fix bug", "status": "pending", "activeForm": "Fixing bug"},
            {"content": "Run tests", "status": "in_progress", "activeForm": "Running tests"},
        ]
        xml = f'<tool_call name="TodoWrite"><input>{json.dumps({"todos": todos})}</input></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is not None
        parsed = json.loads(m.group(2))
        assert len(parsed["todos"]) == 2

    def test_no_match_on_unknown_inner_tag(self):
        """Unknown inner tag should NOT match primary regex (falls to fallback)."""
        xml = '<tool_call name="Read"><custom_tag>{"file_path": "/a.py"}</custom_tag></tool_call>'
        m = _TOOL_CALL_RE.search(xml)
        assert m is None  # Should fall through to fallback


# ═══════════════════════════════════════════════════════════════════════
# 2. _TOOL_CALL_FALLBACK_RE — Any inner tag pair with backreference
# ═══════════════════════════════════════════════════════════════════════

class TestToolCallFallbackRE:
    """Fallback regex: <tool_call name="X"><ANY_TAG>{json}</ANY_TAG></tool_call>"""

    def test_custom_inner_tag(self):
        xml = '<tool_call name="Read"><custom_tag>{"file_path": "/a.py"}</custom_tag></tool_call>'
        m = _TOOL_CALL_FALLBACK_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"
        assert m.group(2) == "custom_tag"  # captured inner tag name
        parsed = json.loads(m.group(3))
        assert parsed["file_path"] == "/a.py"

    def test_backreference_rejects_mismatched(self):
        """Mismatched tags should not match."""
        xml = '<tool_call name="Read"><input>{"file_path": "/a.py"}</output></tool_call>'
        m = _TOOL_CALL_FALLBACK_RE.search(xml)
        assert m is None

    def test_reasoning_skip_in_fallback(self):
        xml = (
            '<tool_call name="Read">'
            '<reasoning>Thinking...</reasoning>'
            '<custom>{"file_path": "/a.py"}</custom>'
            '</tool_call>'
        )
        m = _TOOL_CALL_FALLBACK_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"

    def test_single_quotes_name(self):
        xml = """<tool_call name='Bash'><custom>{"command": "ls"}</custom></tool_call>"""
        m = _TOOL_CALL_FALLBACK_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Bash"


# ═══════════════════════════════════════════════════════════════════════
# 3. _TOOL_CALL_BARE_RE — No inner tags, bare JSON
# ═══════════════════════════════════════════════════════════════════════

class TestToolCallBareRE:
    """Bare regex: <tool_call name="X">{json}</tool_call>"""

    def test_bare_json(self):
        xml = '<tool_call name="Read">{"file_path": "/test.py"}</tool_call>'
        m = _TOOL_CALL_BARE_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"
        parsed = json.loads(m.group(2).strip())
        assert parsed["file_path"] == "/test.py"

    def test_empty_input_enterplanmode(self):
        """EnterPlanMode with empty JSON input."""
        xml = '<tool_call name="EnterPlanMode">{}</tool_call>'
        m = _TOOL_CALL_BARE_RE.search(xml)
        assert m is not None
        assert m.group(1) == "EnterPlanMode"
        parsed = json.loads(m.group(2).strip())
        assert parsed == {}

    def test_no_input_at_all(self):
        """Tool call with no input content."""
        xml = '<tool_call name="EnterPlanMode"></tool_call>'
        m = _TOOL_CALL_BARE_RE.search(xml)
        assert m is not None
        assert m.group(1) == "EnterPlanMode"
        assert m.group(2).strip() == ""

    def test_bare_with_whitespace(self):
        xml = '<tool_call name="Read">\n  {"file_path": "/a.py"}\n</tool_call>'
        m = _TOOL_CALL_BARE_RE.search(xml)
        assert m is not None
        parsed = json.loads(m.group(2).strip())
        assert parsed["file_path"] == "/a.py"

    def test_single_quotes_bare(self):
        xml = """<tool_call name='Read'>{"file_path": "/a.py"}</tool_call>"""
        m = _TOOL_CALL_BARE_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"


# ═══════════════════════════════════════════════════════════════════════
# 4. _TOOL_CALL_ARGKV_RE — GLM <arg_key>/<arg_value> format
# ═══════════════════════════════════════════════════════════════════════

class TestToolCallArgkvRE:
    """GLM format: <tool_call>Name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>"""

    def test_single_kv_pair(self):
        xml = '<tool_call>Read<arg_key>file_path</arg_key><arg_value>/test.py</arg_value></tool_call>'
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"

    def test_multiple_kv_pairs(self):
        xml = (
            '<tool_call>Write'
            '<arg_key>file_path</arg_key><arg_value>/test.py</arg_value>'
            '<arg_key>content</arg_key><arg_value>hello world</arg_value>'
            '</tool_call>'
        )
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Write"
        # Verify inner pairs parse correctly
        pairs = list(_ARG_KV_PAIR_RE.finditer(m.group(2)))
        assert len(pairs) == 2
        assert pairs[0].group(1).strip() == "file_path"
        assert pairs[0].group(2) == "/test.py"
        assert pairs[1].group(1).strip() == "content"
        assert pairs[1].group(2) == "hello world"

    def test_whitespace_between_pairs(self):
        xml = (
            '<tool_call>Bash\n'
            '  <arg_key>command</arg_key>\n'
            '  <arg_value>ls -la</arg_value>\n'
            '</tool_call>'
        )
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Bash"

    def test_value_with_special_chars(self):
        xml = (
            '<tool_call>Grep'
            '<arg_key>pattern</arg_key><arg_value>def\\s+main</arg_value>'
            '</tool_call>'
        )
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        pairs = list(_ARG_KV_PAIR_RE.finditer(m.group(2)))
        assert pairs[0].group(2) == "def\\s+main"

    def test_no_match_standard_format(self):
        """Standard format should NOT match GLM regex."""
        xml = '<tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>'
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is None

    def test_three_kv_pairs(self):
        xml = (
            '<tool_call>Edit'
            '<arg_key>file_path</arg_key><arg_value>/test.py</arg_value>'
            '<arg_key>old_string</arg_key><arg_value>foo</arg_value>'
            '<arg_key>new_string</arg_key><arg_value>bar</arg_value>'
            '</tool_call>'
        )
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Edit"
        pairs = list(_ARG_KV_PAIR_RE.finditer(m.group(2)))
        assert len(pairs) == 3


# ═══════════════════════════════════════════════════════════════════════
# 5. _TOOL_DILUTED_RE — <tool_name>/<args> format (post-prompt-dilution)
# ═══════════════════════════════════════════════════════════════════════

class TestToolDilutedRE:
    """Diluted format: <tool_name>X</tool_name><args>{json}</args>"""

    def test_args_tag(self):
        xml = '<tool_name>Read</tool_name><args>{"file_path": "/test.py"}</args>'
        m = _TOOL_DILUTED_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"
        parsed = json.loads(m.group(2))
        assert parsed["file_path"] == "/test.py"

    def test_arguments_tag(self):
        xml = '<tool_name>Bash</tool_name><arguments>{"command": "echo hello"}</arguments>'
        m = _TOOL_DILUTED_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Bash"

    def test_params_tag(self):
        xml = '<tool_name>Grep</tool_name><params>{"pattern": "TODO"}</params>'
        m = _TOOL_DILUTED_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Grep"

    def test_input_tag(self):
        xml = '<tool_name>Read</tool_name><input>{"file_path": "/a.py"}</input>'
        m = _TOOL_DILUTED_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Read"

    def test_whitespace_between_tags(self):
        xml = '<tool_name>Read</tool_name>\n<args>{"file_path": "/a.py"}</args>'
        m = _TOOL_DILUTED_RE.search(xml)
        assert m is not None

    def test_no_match_standard_format(self):
        """Standard format should NOT match diluted regex."""
        xml = '<tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>'
        m = _TOOL_DILUTED_RE.search(xml)
        assert m is None

    def test_no_match_unknown_inner_tag(self):
        """Only args/arguments/params/input are valid."""
        xml = '<tool_name>Read</tool_name><custom>{"file_path": "/a.py"}</custom>'
        m = _TOOL_DILUTED_RE.search(xml)
        assert m is None


# ═══════════════════════════════════════════════════════════════════════
# 6. _strip_inner_xml_tags
# ═══════════════════════════════════════════════════════════════════════

class TestStripInnerXmlTags:

    def test_plain_json(self):
        assert _strip_inner_xml_tags('{"a": 1}') == '{"a": 1}'

    def test_input_wrapper(self):
        assert _strip_inner_xml_tags('<input>{"a": 1}</input>') == '{"a": 1}'

    def test_textarea_wrapper(self):
        assert _strip_inner_xml_tags('<textarea>{"a": 1}</textarea>') == '{"a": 1}'

    def test_cdata_wrapper(self):
        assert _strip_inner_xml_tags('<![CDATA[{"a": 1}]]>') == '{"a": 1}'

    def test_cdata_inside_input(self):
        assert _strip_inner_xml_tags('<input><![CDATA[{"a": 1}]]></input>') == '{"a": 1}'

    def test_reasoning_stripped(self):
        raw = '<reasoning>I should do X</reasoning>{"file_path": "/a.py"}'
        result = _strip_inner_xml_tags(raw)
        assert "<reasoning>" not in result
        parsed = json.loads(result)
        assert parsed["file_path"] == "/a.py"

    def test_reasoning_then_input(self):
        raw = '<reasoning>Thinking</reasoning><input>{"a": 1}</input>'
        result = _strip_inner_xml_tags(raw)
        assert "<reasoning>" not in result


# ═══════════════════════════════════════════════════════════════════════
# 7. _safe_parse_tool_input
# ═══════════════════════════════════════════════════════════════════════

class TestSafeParseToolInput:

    def test_valid_json(self):
        result = _safe_parse_tool_input('{"file_path": "/a.py"}', "Read")
        assert result == {"file_path": "/a.py"}

    def test_empty_string(self):
        assert _safe_parse_tool_input("", "Read") == {}

    def test_malformed_json_repaired(self):
        """Missing closing brace."""
        result = _safe_parse_tool_input('{"file_path": "/a.py"', "Read")
        assert isinstance(result, dict)
        assert "file_path" in result

    def test_single_quotes_json(self):
        """Some models use single quotes."""
        result = _safe_parse_tool_input("{'file_path': '/a.py'}", "Read")
        assert isinstance(result, dict)

    def test_wrapped_input_tag(self):
        result = _safe_parse_tool_input('<input>{"file_path": "/a.py"}</input>', "Read")
        assert isinstance(result, dict)
        assert result.get("file_path") == "/a.py"

    def test_reasoning_then_json(self):
        raw = '<reasoning>I will read</reasoning>{"file_path": "/a.py"}'
        result = _safe_parse_tool_input(raw, "Read")
        assert result.get("file_path") == "/a.py"

    def test_never_raises(self):
        """Should never raise, even with garbage input."""
        result = _safe_parse_tool_input("completely invalid <<<>>>!!!", "Read")
        assert isinstance(result, dict)

    def test_array_input_wrapped(self):
        """TodoWrite sends an array, should be wrapped as {"value": [...]}."""
        todos = [{"content": "x", "status": "pending", "activeForm": "y"}]
        result = _safe_parse_tool_input(json.dumps(todos), "TodoWrite", tools=TOOLS)
        assert isinstance(result, dict)

    def test_unicode_values(self):
        result = _safe_parse_tool_input('{"file_path": "/日本語/файл.py"}', "Read")
        assert "日本語" in result["file_path"]


# ═══════════════════════════════════════════════════════════════════════
# 8. extract_tool_calls_from_text — Full cascade
# ═══════════════════════════════════════════════════════════════════════

class TestExtractToolCallsFromText:
    """Test the full extraction cascade with valid tool name filtering."""

    def test_standard_format(self):
        text = 'Some text <tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call> more text'
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Read"
        assert blocks[0]["input"]["file_path"] == "/a.py"
        assert "<tool_call" not in remaining

    def test_fallback_format(self):
        text = '<tool_call name="Read"><custom>{"file_path": "/a.py"}</custom></tool_call>'
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Read"

    def test_bare_format(self):
        text = '<tool_call name="Read">{"file_path": "/a.py"}</tool_call>'
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Read"

    def test_glm_argkv_format(self):
        text = '<tool_call>Read<arg_key>file_path</arg_key><arg_value>/a.py</arg_value></tool_call>'
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Read"
        assert blocks[0]["input"]["file_path"] == "/a.py"

    def test_diluted_format(self):
        text = '<tool_name>Read</tool_name><args>{"file_path": "/a.py"}</args>'
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Read"

    def test_single_quote_name_extraction(self):
        """DeepSeek-reasoner single-quote name= in extract flow."""
        text = """<tool_call name='Read'><input>{"file_path": "/test.py"}</input></tool_call>"""
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Read"

    def test_multiple_tools_in_text(self):
        text = (
            'Step 1: <tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>\n'
            'Step 2: <tool_call name="Bash"><input>{"command": "ls"}</input></tool_call>'
        )
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 2
        names = {b["name"] for b in blocks}
        assert names == {"Read", "Bash"}

    def test_hallucinated_tool_filtered(self):
        text = '<tool_call name="FakeToolThatDoesNotExist"><input>{"x": 1}</input></tool_call>'
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 0

    def test_mixed_valid_and_invalid(self):
        text = (
            '<tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>\n'
            '<tool_call name="HallucinatedTool"><input>{"x": 1}</input></tool_call>'
        )
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Read"

    def test_empty_text(self):
        blocks, remaining = extract_tool_calls_from_text("", valid_tool_names=VALID_NAMES)
        assert blocks == []
        assert remaining == ""

    def test_no_tools_in_text(self):
        blocks, remaining = extract_tool_calls_from_text("Just regular text with no XML", valid_tool_names=VALID_NAMES)
        assert blocks == []
        assert remaining == "Just regular text with no XML"

    def test_reasoning_inside_tool_call_extraction(self):
        """<reasoning> inside <tool_call> should not break extraction."""
        text = (
            '<tool_call name="Read">'
            '<reasoning>Need to check the file</reasoning>'
            '<input>{"file_path": "/test.py"}</input>'
            '</tool_call>'
        )
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Read"
        assert blocks[0]["input"]["file_path"] == "/test.py"

    def test_todowrite_complex_json(self):
        """TodoWrite with nested array — real CC format."""
        todos = json.dumps({"todos": [
            {"content": "Fix bug", "status": "pending", "activeForm": "Fixing"},
            {"content": "Test", "status": "in_progress", "activeForm": "Testing"},
        ]})
        text = f'<tool_call name="TodoWrite"><input>{todos}</input></tool_call>'
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "TodoWrite"
        assert len(blocks[0]["input"]["todos"]) == 2

    def test_tool_use_id_generated(self):
        text = '<tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>'
        blocks, _ = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["id"].startswith("toolu_")

    def test_text_preserved_around_tool(self):
        text = 'I will read the file. <tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call> Done.'
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert "I will read the file" in remaining
        assert "Done" in remaining


# ═══════════════════════════════════════════════════════════════════════
# 9. XmlToolBuffer — Streaming state machine
# ═══════════════════════════════════════════════════════════════════════

class TestXmlToolBuffer:
    """XmlToolBuffer tests for incremental parsing."""

    def test_complete_tool_in_one_chunk(self):
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        xml = '<tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>'
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Read"

    def test_tool_split_across_chunks(self):
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        s1 = buf.feed('<tool_call name="Re')
        s2 = buf.feed('ad"><input>{"file_path": "/a.py"}</input></tool_call>')
        all_segments = s1 + s2
        tools = [s for s in all_segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Read"

    def test_text_before_tool(self):
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed('Some text <tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>')
        texts = [s for s in segments if s["type"] == "text"]
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(texts) >= 1
        assert any("Some text" in t["text"] for t in texts)
        assert len(tools) == 1

    def test_text_after_tool(self):
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        s1 = buf.feed('<tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>After text')
        s2 = buf.flush()
        all_segments = s1 + s2
        tools = [s for s in all_segments if s["type"] == "tool_call"]
        texts = [s for s in all_segments if s["type"] == "text"]
        assert len(tools) == 1
        assert any("After text" in t["text"] for t in texts)

    def test_multiple_tools(self):
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        xml = (
            '<tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>'
            '<tool_call name="Bash"><input>{"command": "ls"}</input></tool_call>'
        )
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 2
        assert tools[0]["name"] == "Read"
        assert tools[1]["name"] == "Bash"

    def test_backtick_quoted_not_matched(self):
        """tool_call inside backticks (documentation) should NOT be detected."""
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        text = 'Use `<tool_call name="Read">` to read files.'
        segments = buf.feed(text)
        s_flush = buf.flush()
        all_segments = segments + s_flush
        tools = [s for s in all_segments if s["type"] == "tool_call"]
        assert len(tools) == 0

    def test_hallucinated_tool_emitted_as_text(self):
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        xml = '<tool_call name="FakeHallucinatedTool"><input>{"x": 1}</input></tool_call>'
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        texts = [s for s in segments if s["type"] == "text"]
        assert len(tools) == 0
        assert len(texts) >= 1

    def test_flush_incomplete_tool(self):
        """Incomplete tool call at stream end → incomplete_tool_call segment."""
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        buf.feed('<tool_call name="Read"><input>{"file_path": "/a.py"')
        segments = buf.flush()
        types = [s["type"] for s in segments]
        assert "incomplete_tool_call" in types

    def test_glm_format_in_buffer(self):
        """GLM argkv format parsed through XmlToolBuffer."""
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        xml = '<tool_call>Read<arg_key>file_path</arg_key><arg_value>/a.py</arg_value></tool_call>'
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Read"
        assert tools[0]["input"]["file_path"] == "/a.py"

    def test_buffer_overflow_safety(self):
        """Buffer growing past 16KB without </tool_call> → emit as text."""
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        buf.feed('<tool_call name="Read"><input>')
        # Feed 20KB of data without closing tag
        large_data = "x" * 20000
        segments = buf.feed(large_data)
        # Buffer should have been flushed as text
        texts = [s for s in segments if s["type"] == "text"]
        assert len(texts) >= 1

    def test_empty_feed(self):
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed("")
        assert segments == []

    def test_empty_flush(self):
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.flush()
        assert segments == []


# ═══════════════════════════════════════════════════════════════════════
# 10. strip_tool_call_xml — Last-resort cleanup
# ═══════════════════════════════════════════════════════════════════════

class TestStripToolCallXml:

    def test_strips_standard(self):
        text = 'Hello <tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call> world'
        result = strip_tool_call_xml(text)
        assert "<tool_call" not in result
        assert "Hello" in result
        assert "world" in result

    def test_strips_argkv(self):
        text = '<tool_call>Read<arg_key>file_path</arg_key><arg_value>/a.py</arg_value></tool_call>'
        result = strip_tool_call_xml(text)
        assert "<tool_call" not in result
        assert "<arg_key>" not in result

    def test_strips_orphaned_tags(self):
        """Orphaned inner tags are stripped when <tool_call is present in text."""
        text = 'Text with <tool_call orphaned </tool_call> and <input> tags'
        result = strip_tool_call_xml(text)
        assert "</tool_call>" not in result
        assert "<input>" not in result

    def test_no_modification_when_no_xml(self):
        text = "Regular text without any tool XML"
        assert strip_tool_call_xml(text) == text

    def test_empty_string(self):
        assert strip_tool_call_xml("") == ""

    def test_none_input(self):
        assert strip_tool_call_xml(None) is None


# ═══════════════════════════════════════════════════════════════════════
# 11. recover_truncated_deterministic
# ═══════════════════════════════════════════════════════════════════════

class TestRecoverTruncatedDeterministic:

    def test_truncated_json_repaired(self):
        """Truncated JSON with all required fields → recovery succeeds."""
        partial = '<tool_call name="Read"><input>{"file_path": "/a.py"'
        result = recover_truncated_deterministic(partial, tools=TOOLS)
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "Read"
        assert result[0]["input"]["file_path"] == "/a.py"

    def test_truncated_missing_required(self):
        """Truncated JSON missing required fields → returns None."""
        partial = '<tool_call name="Write"><input>{"file_path": "/a.py"'
        # Write requires file_path + content — content is missing
        result = recover_truncated_deterministic(partial, tools=TOOLS)
        # Should be None because "content" is missing
        assert result is None

    def test_empty_input(self):
        assert recover_truncated_deterministic("", tools=TOOLS) is None

    def test_no_tool_call_tag(self):
        assert recover_truncated_deterministic("just text", tools=TOOLS) is None

    def test_argkv_truncated(self):
        """GLM format truncated but with complete kv pairs."""
        partial = '<tool_call>Read<arg_key>file_path</arg_key><arg_value>/a.py</arg_value>'
        result = recover_truncated_deterministic(partial, tools=TOOLS)
        assert result is not None
        assert result[0]["name"] == "Read"
        assert result[0]["input"]["file_path"] == "/a.py"


# ═══════════════════════════════════════════════════════════════════════
# 12. validate_tool_name + _build_valid_tool_names
# ═══════════════════════════════════════════════════════════════════════

class TestToolNameValidation:

    def test_valid_name(self):
        assert validate_tool_name("Read", VALID_NAMES) is True

    def test_invalid_name(self):
        assert validate_tool_name("FakeTool", VALID_NAMES) is False

    def test_empty_name(self):
        assert validate_tool_name("", VALID_NAMES) is False

    def test_none_name(self):
        assert validate_tool_name(None, VALID_NAMES) is False

    def test_no_allowlist(self):
        """No allowlist → everything passes (backward compat)."""
        assert validate_tool_name("AnyTool", set()) is True

    def test_build_from_dicts(self):
        names = _build_valid_tool_names(TOOLS)
        assert "Read" in names
        assert "Write" in names
        assert len(names) == len(TOOLS)

    def test_build_from_none(self):
        assert _build_valid_tool_names(None) == set()

    def test_build_from_empty(self):
        assert _build_valid_tool_names([]) == set()


# ═══════════════════════════════════════════════════════════════════════
# 13. _normalize_escaped_xml
# ═══════════════════════════════════════════════════════════════════════

class TestNormalizeEscapedXml:
    """Tests for the escaped XML normalization helper."""

    def test_no_escaping_returns_none(self):
        xml = '<tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>'
        assert _normalize_escaped_xml(xml) is None

    def test_escaped_quotes(self):
        xml = '<tool_call name=\\"Read\\"><input>{\\"file_path\\": \\"/a.py\\"}</input></tool_call>'
        result = _normalize_escaped_xml(xml)
        assert result is not None
        assert '\\"' not in result
        assert 'name="Read"' in result

    def test_escaped_newlines(self):
        xml = '<tool_call name="Read">\\n<input>{"file_path": "/a.py"}</input>\\n</tool_call>'
        result = _normalize_escaped_xml(xml)
        assert result is not None
        assert '\\n' not in result
        assert '\n' in result

    def test_double_escaped(self):
        xml = '<tool_call name=\\\\"Read\\\\">'
        result = _normalize_escaped_xml(xml)
        assert result is not None
        assert 'name="Read"' in result

    def test_mixed_escaping(self):
        xml = '<tool_call name=\\"Read\\">\\n<input>{\\"file_path\\": \\"/a.py\\"}</input>\\n</tool_call>'
        result = _normalize_escaped_xml(xml)
        assert result is not None
        assert 'name="Read"' in result
        assert '"file_path"' in result


# ═══════════════════════════════════════════════════════════════════════
# 14. _REAL_NAME_RE — Distinguishes real vs escaped tool_call opens
# ═══════════════════════════════════════════════════════════════════════

class TestRealNameRE:
    """Tests for _REAL_NAME_RE that detects unescaped quotes in name= attr."""

    def test_double_quotes(self):
        assert _REAL_NAME_RE.search('<tool_call name="Read">') is not None

    def test_single_quotes(self):
        assert _REAL_NAME_RE.search("<tool_call name='Read'>") is not None

    def test_escaped_quotes_no_match(self):
        assert _REAL_NAME_RE.search('<tool_call name=\\"Read\\">') is None

    def test_no_name_attr(self):
        assert _REAL_NAME_RE.search('<tool_call>Read') is None


# ═══════════════════════════════════════════════════════════════════════
# 15. XmlToolBuffer — Nested </tool_call> and escaped XML scenarios
# ═══════════════════════════════════════════════════════════════════════

class TestXmlToolBufferNestedAndEscaped:
    """Tests for the v4 _try_extract_tool algorithm handling nested/escaped XML."""

    def test_write_with_inner_tool_call_complete(self):
        """Write tool whose JSON content contains </tool_call> — complete case.

        The algorithm must skip the inner </tool_call> (inside JSON content)
        and match the outer closing tag.
        """
        inner_content = (
            'Here is how to use tools:\\n'
            '<tool_call name="Read">\\n'
            '<input>{"file_path": "/test.py"}</input>\\n'
            '</tool_call>\\n'
            'And another:\\n'
            '<tool_call name="Bash">\\n'
            '<input>{"command": "ls"}</input>\\n'
            '</tool_call>'
        )
        content_json = json.dumps({"file_path": "/doc.md", "content": inner_content})
        xml = f'<tool_call name="Write"><input>{content_json}</input></tool_call>'

        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Write"
        assert tools[0]["input"]["file_path"] == "/doc.md"

    def test_write_with_inner_tool_call_streaming(self):
        """Write tool with inner </tool_call> — streaming incomplete case.

        When the outer </tool_call> hasn't arrived yet, the algorithm should
        WAIT (return None) instead of prematurely extracting.
        """
        # Build partial XML — outer </tool_call> not yet received
        partial = (
            '<tool_call name="Write"><input>'
            '{"file_path": "/doc.md", "content": "example: </tool_call> text"}'
            # Note: no outer </tool_call> yet
        )
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed(partial)
        tools = [s for s in segments if s["type"] == "tool_call"]
        # Should NOT have extracted prematurely — still waiting
        assert len(tools) == 0
        assert buf.in_tool is True

        # Now send the real closing tag
        segments2 = buf.feed('</input></tool_call>')
        tools2 = [s for s in segments2 if s["type"] == "tool_call"]
        assert len(tools2) == 1
        assert tools2[0]["name"] == "Write"

    def test_escaped_xml_normalized_and_parsed(self):
        """Escaped XML (from JSON content leak) — normalized to valid XML and parsed.

        This simulates what happens when a Write tool's content with escaped
        tool_call XML gets separated from its wrapper after premature extraction.
        """
        escaped_xml = (
            '<tool_call name=\\"Read\\">\\n'
            '<input>{\\"file_path\\": \\"/test.py\\"}</input>\\n'
            '</tool_call>'
        )
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed(escaped_xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Read"
        assert tools[0]["input"]["file_path"] == "/test.py"

    def test_double_escaped_xml_normalized(self):
        """Double-escaped XML — two rounds of unescaping needed."""
        double_escaped = (
            '<tool_call name=\\\\"Read\\\\">\\n'
            '<input>{\\\\"file_path\\\\": \\\\"/test.py\\\\"}</input>\\n'
            '</tool_call>'
        )
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed(double_escaped)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Read"

    def test_write_with_two_inner_tool_calls(self):
        """Write tool with TWO inner </tool_call> tags in content."""
        content = (
            'Tool 1: <tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>\\n'
            'Tool 2: <tool_call name="Bash"><input>{"command": "ls"}</input></tool_call>'
        )
        content_json = json.dumps({"file_path": "/doc.md", "content": content})
        xml = f'<tool_call name="Write"><input>{content_json}</input></tool_call>'

        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Write"

    def test_streaming_realistic_chunked(self):
        """Simulate realistic streaming — tool XML arrives in small chunks."""
        full_xml = '<tool_call name="Read"><input>{"file_path": "/test.py"}</input></tool_call>'
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)

        all_segments = []
        # Feed in chunks of 5-10 characters
        chunk_size = 7
        for i in range(0, len(full_xml), chunk_size):
            chunk = full_xml[i:i + chunk_size]
            all_segments.extend(buf.feed(chunk))

        tools = [s for s in all_segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Read"
        assert tools[0]["input"]["file_path"] == "/test.py"

    def test_documentation_text_not_matched_as_tool(self):
        """Text mentioning <tool_call> and </tool_call> in documentation context."""
        doc_text = (
            'You can use `<tool_call name="Read">` and `</tool_call>` tags '
            'to invoke tools in the XML format.'
        )
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed(doc_text)
        flush_segments = buf.flush()
        all_segments = segments + flush_segments
        tools = [s for s in all_segments if s["type"] == "tool_call"]
        assert len(tools) == 0

    def test_sequential_tools_after_nested(self):
        """After processing a Write with nested content, next tool parses correctly."""
        content_json = json.dumps({"file_path": "/doc.md", "content": "example </tool_call>"})
        xml1 = f'<tool_call name="Write"><input>{content_json}</input></tool_call>'
        xml2 = '<tool_call name="Read"><input>{"file_path": "/a.py"}</input></tool_call>'

        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed(xml1 + xml2)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 2
        assert tools[0]["name"] == "Write"
        assert tools[1]["name"] == "Read"

    def test_sequential_tools_after_full_nested_xml(self):
        """Write with full <tool_call>...<input>...</input></tool_call> in JSON + subsequent Read.

        The greedy regex must be bounded to the first tool only, otherwise it
        consumes the subsequent Read tool's XML as part of Write's content.
        """
        inner = '<tool_call name="Bash"><input>{"command":"pwd"}</input></tool_call>'
        content_json = json.dumps({"file_path": "/doc.md", "content": inner})
        xml = (
            f'<tool_call name="Write">\n<input>\n{content_json}\n</input>\n</tool_call>\n'
            '<tool_call name="Read">\n<input>\n{"file_path": "/a.py"}\n</input>\n</tool_call>'
        )

        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        segments = buf.feed(xml)
        segments.extend(buf.flush())
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 2
        assert tools[0]["name"] == "Write"
        assert tools[0]["input"]["file_path"] == "/doc.md"
        assert tools[1]["name"] == "Read"
        assert tools[1]["input"]["file_path"] == "/a.py"


# ═══════════════════════════════════════════════════════════════════════
# 16. XML-as-tags parsing — model uses XML param tags instead of JSON
# ═══════════════════════════════════════════════════════════════════════

class TestXmlAsTagsParsing:
    """Tests for _parse_xml_as_tags and its integration with the parsing pipeline."""

    def test_read_single_param(self):
        """Read with <file_path> tag instead of JSON."""
        raw = "<file_path>/src/main.py</file_path>"
        result = _parse_xml_as_tags(raw, "Read", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/src/main.py"

    def test_write_multi_param(self):
        """Write with <file_path> + <content> tags."""
        raw = "<file_path>/src/main.py</file_path>\n<content>print('hello')</content>"
        result = _parse_xml_as_tags(raw, "Write", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/src/main.py"
        assert result["content"] == "print('hello')"

    def test_edit_three_params(self):
        """Edit with 3 XML param tags."""
        raw = (
            "<file_path>/src/main.py</file_path>\n"
            "<old_string>hello</old_string>\n"
            "<new_string>world</new_string>"
        )
        result = _parse_xml_as_tags(raw, "Edit", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/src/main.py"
        assert result["old_string"] == "hello"
        assert result["new_string"] == "world"

    def test_schema_validation_rejects_random_tags(self):
        """Tags that don't overlap with tool schema are rejected."""
        raw = "<foo>bar</foo>\n<baz>qux</baz>"
        result = _parse_xml_as_tags(raw, "Read", tools=TOOLS)
        assert result is None

    def test_no_tools_schema_accepts_any(self):
        """Without tools schema, any XML-as-tags are accepted."""
        raw = "<file_path>/src/main.py</file_path>"
        result = _parse_xml_as_tags(raw, "Read", tools=None)
        assert result is not None
        assert result["file_path"] == "/src/main.py"

    def test_skips_known_inner_tags(self):
        """Known inner tags like <input>, <reasoning> are not treated as params."""
        raw = "<input>{\"file_path\": \"/test.py\"}</input>"
        result = _parse_xml_as_tags(raw, "Read", tools=TOOLS)
        assert result is None

    def test_plain_json_not_matched(self):
        """Plain JSON without XML tags returns None."""
        raw = '{"file_path": "/test.py"}'
        result = _parse_xml_as_tags(raw, "Read", tools=TOOLS)
        assert result is None

    def test_integration_safe_parse(self):
        """XML-as-tags detected in _safe_parse_tool_input pipeline."""
        raw = "<file_path>/src/main.py</file_path>\n<content>hello world</content>"
        result = _safe_parse_tool_input(raw, "Write", tools=TOOLS)
        assert result["file_path"] == "/src/main.py"
        assert result["content"] == "hello world"

    def test_integration_extract_bare_format(self):
        """XML-as-tags via BARE regex in extract_tool_calls_from_text."""
        text = (
            '<tool_call name="Write">\n'
            "<file_path>/path/to/file.md</file_path>\n"
            "<content># Hello</content>\n"
            "</tool_call>"
        )
        blocks, remaining = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Write"
        assert blocks[0]["input"]["file_path"] == "/path/to/file.md"
        assert blocks[0]["input"]["content"] == "# Hello"

    def test_recovery_xml_as_tags_truncated(self):
        """Truncated XML-as-tags recovered by recover_truncated_deterministic."""
        partial = (
            '<tool_call name="Write">\n'
            "<file_path>/path/to/file.md</file_path>\n"
            "<content># Some truncated content..."
        )
        result = recover_truncated_deterministic(partial, tools=TOOLS)
        assert result is not None
        assert result[0]["name"] == "Write"
        assert result[0]["input"]["file_path"] == "/path/to/file.md"
        assert "truncated content" in result[0]["input"]["content"]

    def test_recovery_xml_as_tags_complete(self):
        """Complete XML-as-tags (all required fields present) recovered."""
        partial = (
            '<tool_call name="Read">\n'
            "<file_path>/src/main.py</file_path>\n"
        )
        result = recover_truncated_deterministic(partial, tools=TOOLS)
        assert result is not None
        assert result[0]["name"] == "Read"
        assert result[0]["input"]["file_path"] == "/src/main.py"


# ═══════════════════════════════════════════════════════════════════════
# 17. Repair hardening — list unwrap
# ═══════════════════════════════════════════════════════════════════════

class TestRepairListUnwrap:
    """Tests for _repair_tool_input list unwrap fix."""

    def test_single_element_list_unwrapped_to_string(self):
        """Single-element list unwrapped when schema expects string."""
        result = _repair_tool_input("Read", {"value": ["/src/main.py"]}, TOOLS)
        assert result == {"file_path": "/src/main.py"}

    def test_multi_element_list_not_unwrapped(self):
        """Multi-element list stays as {"value": [...]}."""
        result = _repair_tool_input("Read", {"value": ["/a.py", "/b.py"]}, TOOLS)
        assert "value" in result

    def test_list_of_dicts_for_array_field(self):
        """List of dicts rewrapped to array field (TodoWrite)."""
        todos = [{"content": "Fix", "status": "pending", "activeForm": "Fixing"}]
        result = _repair_tool_input("TodoWrite", {"value": todos}, TOOLS)
        assert result == {"todos": todos}


# ═══════════════════════════════════════════════════════════════════════
# 18. Schema helpers — DRY refactor
# ═══════════════════════════════════════════════════════════════════════

class TestSchemaHelpers:
    """Tests for _get_tool_schema, _get_tool_properties."""

    def test_get_tool_schema_found(self):
        schema = _get_tool_schema("Read", TOOLS)
        assert schema is not None
        assert "properties" in schema

    def test_get_tool_schema_not_found(self):
        assert _get_tool_schema("Nonexistent", TOOLS) is None

    def test_get_tool_schema_none_tools(self):
        assert _get_tool_schema("Read", None) is None

    def test_get_tool_properties(self):
        props = _get_tool_properties("Write", TOOLS)
        assert "file_path" in props
        assert "content" in props

    def test_get_tool_properties_empty(self):
        props = _get_tool_properties("EnterPlanMode", TOOLS)
        assert props == {}


# ═══════════════════════════════════════════════════════════════════════
# 19. Few-shot prompting
# ═══════════════════════════════════════════════════════════════════════

class TestFewShotPrompting:
    """Tests for few-shot examples and prompt anti-XML-as-tags rule."""

    def test_few_shots_include_present_tools(self):
        examples = _build_few_shot_examples(TOOLS)
        assert 'name="Read"' in examples
        assert 'name="Write"' in examples
        assert 'name="Bash"' in examples

    def test_few_shots_exclude_absent_tools(self):
        """Tools not in request are not included in few-shots."""
        minimal_tools = [TOOLS[0]]  # Only Read
        examples = _build_few_shot_examples(minimal_tools)
        assert 'name="Read"' in examples
        assert 'name="Write"' not in examples

    def test_few_shots_have_input_tags(self):
        """All few-shots use <input> tags, not XML param tags."""
        examples = _build_few_shot_examples(TOOLS)
        assert "<input>" in examples
        assert "<file_path>" not in examples.split("WRONG")[0]  # Before WRONG section

    def test_few_shots_have_negative_example(self):
        """Few-shots include negative example warning against XML-as-tags."""
        examples = _build_few_shot_examples(TOOLS)
        assert "WRONG FORMAT" in examples
        assert "<file_path>" in examples  # In the WRONG section

    def test_prompt_has_anti_xml_tags_rule(self):
        """build_tool_prompt includes anti-XML-as-tags CRITICAL rule."""
        prompt = build_tool_prompt(TOOLS)
        assert "NEVER use XML tags for parameters" in prompt

    def test_prompt_fallback_generic_example(self):
        """When no core tools match, generic example is used."""
        exotic_tools = [{"name": "CustomTool", "description": "Custom", "input_schema": {
            "type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]
        }}]
        examples = _build_few_shot_examples(exotic_tools)
        assert 'name="ToolName"' in examples

    def test_prompt_has_anti_parameter_attr_rule(self):
        """Prompt warns against <parameter name="X"> format."""
        prompt = build_tool_prompt(TOOLS)
        assert '<parameter name="X">' in prompt

    def test_few_shots_have_parameter_attr_negative(self):
        """Negative examples include <parameter> attributed format."""
        examples = _build_few_shot_examples(TOOLS)
        assert '<parameter name="file_path">' in examples


# ── Tests for attributed XML parameter format ────────────────────────────

class TestXmlAttrParamParsing:
    """Tests for <parameter name="X">value</parameter> format (Anthropic SDK style)."""

    def test_attr_regex_matches_single(self):
        """_XML_ATTR_PARAM_RE captures name attribute and content."""
        raw = '<parameter name="file_path">/src/main.py</parameter>'
        match = _XML_ATTR_PARAM_RE.search(raw)
        assert match is not None
        assert match.group(1) == "file_path"
        assert match.group(2) == "/src/main.py"

    def test_attr_regex_single_quotes(self):
        """_XML_ATTR_PARAM_RE handles single-quoted name attribute."""
        raw = "<parameter name='command'>ls -la</parameter>"
        match = _XML_ATTR_PARAM_RE.search(raw)
        assert match is not None
        assert match.group(1) == "command"
        assert match.group(2) == "ls -la"

    def test_parse_xml_as_tags_attr_single(self):
        """_parse_xml_as_tags handles single <parameter name="X"> tag."""
        raw = '<parameter name="file_path">/src/main.py</parameter>'
        result = _parse_xml_as_tags(raw, "Read", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/src/main.py"

    def test_parse_xml_as_tags_attr_multi(self):
        """_parse_xml_as_tags handles multiple <parameter> tags for Write."""
        raw = (
            '<parameter name="file_path">/src/main.py</parameter>\n'
            '<parameter name="content">print("hello")</parameter>'
        )
        result = _parse_xml_as_tags(raw, "Write", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/src/main.py"
        assert result["content"] == 'print("hello")'

    def test_parse_xml_as_tags_attr_schema_rejects_random(self):
        """_parse_xml_as_tags rejects attributed tags that don't match schema."""
        raw = '<parameter name="xyz">value</parameter>'
        result = _parse_xml_as_tags(raw, "Read", tools=TOOLS)
        assert result is None  # "xyz" not in Read schema

    def test_safe_parse_integration_attr(self):
        """Full pipeline: _safe_parse_tool_input handles attributed format."""
        raw = '<parameter name="file_path">/src/main.py</parameter>'
        result = _safe_parse_tool_input(raw, "Read", tools=TOOLS)
        assert result["file_path"] == "/src/main.py"

    def test_recovery_attr_complete(self):
        """recover_truncated_deterministic handles complete attributed format."""
        xml = (
            '<tool_call name="Read">\n'
            '<parameter name="file_path">/src/main.py</parameter>\n'
        )
        result = recover_truncated_deterministic(xml, tools=TOOLS)
        assert result is not None
        assert result[0]["name"] == "Read"
        assert result[0]["input"]["file_path"] == "/src/main.py"

    def test_plain_tags_preferred_over_attr(self):
        """When both formats could match, plain tags take priority."""
        raw = "<file_path>/src/main.py</file_path>"
        result = _parse_xml_as_tags(raw, "Read", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/src/main.py"


# ═══════════════════════════════════════════════════════════════════════
# Greedy JSON extraction (Fix 3)
# ═══════════════════════════════════════════════════════════════════════

class TestGreedyExtractJsonFields:
    """Tests for _greedy_extract_json_fields — rescues Write/Edit with broken JSON."""

    def test_write_unescaped_quotes_in_content(self):
        """Write with unescaped double quotes in content field (DeepSeek bug)."""
        raw = '{"file_path": "/tmp/test.md", "content": "Use <tool_call name="Read"> to read files"}'
        result = _greedy_extract_json_fields(raw, "Write", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/tmp/test.md"
        assert "Read" in result["content"]

    def test_write_valid_json_not_needed(self):
        """Valid JSON doesn't need greedy extraction (returns None, let json.loads handle)."""
        # This function is only called when json.loads/repair_json already failed,
        # but if called with valid JSON structure it should still work
        raw = '{"file_path": "/tmp/test.md", "content": "simple content"}'
        result = _greedy_extract_json_fields(raw, "Write", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/tmp/test.md"
        assert result["content"] == "simple content"

    def test_write_with_embedded_xml_examples(self):
        """Write containing XML tool examples in markdown content."""
        raw = (
            '{"file_path": "/tmp/resumen.md", "content": "# Resumen\\n\\n'
            'Formato: <tool_call name="Read"><input>{\\"file_path\\": \\"/test\\"}</input></tool_call>\\n'
            'Eso es todo."}'
        )
        result = _greedy_extract_json_fields(raw, "Write", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/tmp/resumen.md"
        assert "<tool_call" in result["content"]

    def test_write_with_regex_patterns(self):
        """Write with regex patterns containing quotes (name=["'])."""
        raw = '{"file_path": "/tmp/doc.md", "content": "regex: name=["\'](.*?)["\']"}'
        result = _greedy_extract_json_fields(raw, "Write", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/tmp/doc.md"
        assert "name=" in result["content"]

    def test_returns_none_for_single_field_tools(self):
        """Only activates for multi-field tools (Write/Edit), not Read/Bash."""
        raw = '{"file_path": broken json here'
        result = _greedy_extract_json_fields(raw, "Read", tools=TOOLS)
        assert result is None

    def test_returns_none_when_missing_required(self):
        """Returns None when required fields can't be extracted."""
        raw = '{"file_path": "/tmp/test.md"}'  # missing content
        result = _greedy_extract_json_fields(raw, "Write", tools=TOOLS)
        assert result is None

    def test_returns_none_without_tools(self):
        """Returns None when no tools provided."""
        raw = '{"file_path": "/tmp/test.md", "content": "hello"}'
        result = _greedy_extract_json_fields(raw, "Write", tools=None)
        assert result is None

    def test_edit_three_required_fields(self):
        """Edit tool with 3 required string fields."""
        raw = '{"file_path": "/src/main.py", "old_string": "hello", "new_string": "world"}'
        result = _greedy_extract_json_fields(raw, "Edit", tools=TOOLS)
        assert result is not None
        assert result["file_path"] == "/src/main.py"
        assert result["old_string"] == "hello"
        assert result["new_string"] == "world"

    def test_write_newline_unescape(self):
        """Escaped newlines in content are unescaped."""
        raw = '{"file_path": "/tmp/test.md", "content": "line1\\nline2\\n"}'
        result = _greedy_extract_json_fields(raw, "Write", tools=TOOLS)
        assert result is not None
        assert "line1\nline2\n" == result["content"]


# ═══════════════════════════════════════════════════════════════════════
# Schema-aware cleanup (Fix 2)
# ═══════════════════════════════════════════════════════════════════════

class TestSchemaAwareCleanup:
    """Tests for _schema_aware_cleanup — removes extra keys from repair_json garbage."""

    def test_removes_extra_keys(self):
        """Extra keys from repair_json are removed."""
        parsed = {"file_path": "/tmp/test.md", "content": "hello", "Read": "garbage"}
        result = _schema_aware_cleanup(parsed, "Write", tools=TOOLS)
        assert "Read" not in result
        assert result["file_path"] == "/tmp/test.md"
        assert result["content"] == "hello"

    def test_keeps_all_valid_keys(self):
        """Valid keys are preserved untouched."""
        parsed = {"file_path": "/tmp/test.md", "content": "hello"}
        result = _schema_aware_cleanup(parsed, "Write", tools=TOOLS)
        assert result == parsed

    def test_no_cleanup_if_would_lose_required(self):
        """Don't cleanup if it would remove required fields."""
        parsed = {"file_path": "/tmp/test.md", "extra": "val"}  # missing content
        result = _schema_aware_cleanup(parsed, "Write", tools=TOOLS)
        assert result == parsed  # unchanged, cleanup would lose content

    def test_no_tools_returns_original(self):
        """No tools → no cleanup."""
        parsed = {"file_path": "/tmp/test.md", "garbage": "val"}
        result = _schema_aware_cleanup(parsed, "Write", tools=None)
        assert result == parsed


# ═══════════════════════════════════════════════════════════════════════
# Repair tool input Strategy 3 (Fix 5)
# ═══════════════════════════════════════════════════════════════════════

class TestRepairToolInputStrategy3:
    """Tests for _repair_tool_input Strategy 3 — unwrap {"value": dict} for multi-field tools."""

    def test_unwrap_value_dict_for_write(self):
        """{"value": {"file_path": ..., "content": ...}} → unwrapped."""
        input_dict = {"value": {"file_path": "/tmp/test.md", "content": "hello"}}
        result = _repair_tool_input("Write", input_dict, TOOLS)
        assert result == {"file_path": "/tmp/test.md", "content": "hello"}

    def test_unwrap_value_dict_for_edit(self):
        """{"value": {"file_path": ..., "old_string": ..., "new_string": ...}} → unwrapped."""
        inner = {"file_path": "/f.py", "old_string": "a", "new_string": "b"}
        input_dict = {"value": inner}
        result = _repair_tool_input("Edit", input_dict, TOOLS)
        assert result == inner

    def test_no_unwrap_if_missing_required(self):
        """Don't unwrap if inner dict is missing required fields."""
        input_dict = {"value": {"file_path": "/tmp/test.md"}}  # missing content
        result = _repair_tool_input("Write", input_dict, TOOLS)
        assert result == input_dict  # unchanged

    def test_no_unwrap_if_no_overlap(self):
        """Don't unwrap if inner dict keys don't match schema."""
        input_dict = {"value": {"foo": "bar", "baz": "qux"}}
        result = _repair_tool_input("Write", input_dict, TOOLS)
        assert result == input_dict  # unchanged


# ═══════════════════════════════════════════════════════════════════════
# Integration: full pipeline Write with broken JSON (combines all fixes)
# ═══════════════════════════════════════════════════════════════════════

class TestWriteBrokenJsonIntegration:
    """End-to-end: _safe_parse_tool_input with DeepSeek-style broken Write JSON."""

    def test_write_unescaped_xml_in_content(self):
        """Write with unescaped XML examples — greedy extraction saves it."""
        raw = '{"file_path": "/tmp/doc.md", "content": "Use <tool_call name="Read"><input>{"file_path": "/x"}</input></tool_call> format"}'
        result = _safe_parse_tool_input(raw, "Write", tools=TOOLS)
        assert result.get("file_path") == "/tmp/doc.md"
        assert "Read" in result.get("content", "")

    def test_write_repair_produces_extra_keys(self):
        """repair_json adds extra keys → schema cleanup removes them."""
        # Simulate repair_json producing extra keys from split strings
        # This raw will likely fail json.loads and repair_json will try to fix it
        raw = '{"file_path": "/tmp/test.md", "content": "text with "quotes" inside"}'
        result = _safe_parse_tool_input(raw, "Write", tools=TOOLS)
        # Should have file_path at minimum (greedy or repair will extract it)
        assert "file_path" in result
        assert result["file_path"] == "/tmp/test.md"

    def test_xmltoolbuffer_bare_write_with_broken_json(self):
        """XmlToolBuffer: bare Write (no <input> tags) with broken JSON content."""
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        xml = '<tool_call name="Write">{"file_path": "/tmp/x.md", "content": "has <tool_call name="Read"> example"}</tool_call>'
        segments = buf.feed(xml)
        segments += buf.flush()
        tool_segments = [s for s in segments if s["type"] == "tool_call"]
        assert len(tool_segments) >= 1
        tc = tool_segments[0]
        assert tc["name"] == "Write"
        assert "file_path" in tc["input"]
