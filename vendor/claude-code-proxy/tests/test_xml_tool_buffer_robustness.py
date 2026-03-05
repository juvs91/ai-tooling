# tests/test_xml_tool_buffer_robustness.py
"""Robustness tests for ALL malformed XML tool call formats.

Covers real-world malformations observed from GLM-4.7 and DeepSeek-R1:
  1. Double-prefix restart:  <tool_call>G<tool_call>Glob...
  2. Single-char restart:    <tool_call>B<tool_call>Bash...
  3. Whitespace before name: <tool_call>\\nBash\\n<arg_key>...
  4. Newline inside name:    <tool_call>  Read  <arg_key>...
  5. Dotted tool name:       <tool_call>computer.bash<arg_key>...
  6. Dashed tool name:       <tool_call>mcp-tool<arg_key>...
  7. Missing </tool_call>:   <tool_call>Bash<arg_key>k</arg_key><arg_value>v</arg_value>  (EOF)
  8. Truncated mid-arg:      <tool_call>Bash<arg_key>command</arg_key><arg_value>ls  (EOF)
  9. Multi-chunk double-prefix split across SSE chunks
  10. Hallucinated tool name  (not in valid_tool_names) in loose mode → not emitted as tool
  11. strip_tool_call_xml handles loose argkv without closing tag
  12. extract_tool_calls_from_text 5th fallback (loose argkv)
  13. Double-prefix restart does NOT discard if arg tags appeared before nested <tool_call
"""
import pytest
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.tool_prompting import (
    _TOOL_CALL_ARGKV_RE,
    _TOOL_CALL_ARGKV_LOOSE_RE,
    XmlToolBuffer,
    extract_tool_calls_from_text,
    strip_tool_call_xml,
    _build_valid_tool_names,
)

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
    {"name": "Glob", "description": "File glob", "input_schema": {
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"],
    }},
    {"name": "Write", "description": "Write file", "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["file_path", "content"],
    }},
]

VALID_NAMES = _build_valid_tool_names(TOOLS)


def _buf() -> XmlToolBuffer:
    return XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)


# ═══════════════════════════════════════════════════════════════════════
# 1. _TOOL_CALL_ARGKV_RE — updated regex with \s* + [\w.-]+
# ═══════════════════════════════════════════════════════════════════════

class TestArgkvREWhitespaceAndExtendedNames:

    def test_whitespace_before_name_matches(self):
        """\\n before name (GLM streaming artifact) is tolerated by \\s*."""
        xml = "<tool_call>\nBash\n<arg_key>command</arg_key><arg_value>ls</arg_value></tool_call>"
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1).strip() == "Bash"

    def test_spaces_before_name_matches(self):
        xml = "<tool_call>   Read   <arg_key>file_path</arg_key><arg_value>/a.py</arg_value></tool_call>"
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1).strip() == "Read"

    def test_dotted_tool_name_matches(self):
        """Dotted names like computer.bash are matched by [\\w.-]+."""
        xml = "<tool_call>computer.bash<arg_key>command</arg_key><arg_value>echo hi</arg_value></tool_call>"
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1) == "computer.bash"

    def test_dashed_tool_name_matches(self):
        xml = "<tool_call>mcp-tool<arg_key>arg</arg_key><arg_value>value</arg_value></tool_call>"
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1) == "mcp-tool"

    def test_underscore_name_matches(self):
        xml = "<tool_call>my_tool<arg_key>key</arg_key><arg_value>val</arg_value></tool_call>"
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1) == "my_tool"


# ═══════════════════════════════════════════════════════════════════════
# 2. _TOOL_CALL_ARGKV_LOOSE_RE — optional closing tag
# ═══════════════════════════════════════════════════════════════════════

class TestArgkvLooseRE:

    def test_complete_call_matches(self):
        """Loose RE still matches when </tool_call> is present."""
        xml = "<tool_call>Bash<arg_key>command</arg_key><arg_value>ls</arg_value></tool_call>"
        m = _TOOL_CALL_ARGKV_LOOSE_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Bash"

    def test_missing_closing_tag_matches(self):
        """Key: loose RE matches even when </tool_call> is absent at EOF."""
        xml = "<tool_call>Bash<arg_key>command</arg_key><arg_value>ls</arg_value>"
        m = _TOOL_CALL_ARGKV_LOOSE_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Bash"

    def test_whitespace_before_name_in_loose(self):
        xml = "<tool_call>\nRead\n<arg_key>file_path</arg_key><arg_value>/a.py</arg_value>"
        m = _TOOL_CALL_ARGKV_LOOSE_RE.search(xml)
        assert m is not None
        assert m.group(1).strip() == "Read"

    def test_no_match_truncated_arg_value(self):
        """Truncated arg_value (no </arg_value>) must NOT match — prevents partial extraction."""
        xml = "<tool_call>Bash<arg_key>command</arg_key><arg_value>ls"
        m = _TOOL_CALL_ARGKV_LOOSE_RE.search(xml)
        # Complete </arg_value> is required, so this should not match
        assert m is None

    def test_multiple_pairs_missing_closing(self):
        xml = (
            "<tool_call>Write"
            "<arg_key>file_path</arg_key><arg_value>/a.py</arg_value>"
            "<arg_key>content</arg_key><arg_value>hello</arg_value>"
        )
        m = _TOOL_CALL_ARGKV_LOOSE_RE.search(xml)
        assert m is not None
        assert m.group(1) == "Write"


# ═══════════════════════════════════════════════════════════════════════
# 3. XmlToolBuffer — double-prefix restart recovery
# ═══════════════════════════════════════════════════════════════════════

class TestXmlToolBufferDoublePrefixRestart:

    def test_double_prefix_restart_full_in_one_chunk(self):
        """<tool_call>G<tool_call>Glob... — discards prefix, emits Glob tool call."""
        xml = (
            "<tool_call>G"
            "<tool_call>Glob"
            "<arg_key>pattern</arg_key><arg_value>**/*.py</arg_value>"
            "</tool_call>"
        )
        buf = _buf()
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1, f"Expected 1 tool, got: {segments}"
        assert tools[0]["name"] == "Glob"
        assert tools[0]["input"]["pattern"] == "**/*.py"

    def test_double_prefix_single_char_bash(self):
        """<tool_call>B<tool_call>Bash... — single-char prefix discarded."""
        xml = (
            "<tool_call>B"
            "<tool_call>Bash"
            "<arg_key>command</arg_key><arg_value>echo hello</arg_value>"
            "</tool_call>"
        )
        buf = _buf()
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Bash"
        assert tools[0]["input"]["command"] == "echo hello"

    def test_double_prefix_longer_wrong_name(self):
        """<tool_call>GlobXXX<tool_call>Read... — discards fake prefix, recovers Read."""
        xml = (
            "<tool_call>GlobXXX"
            "<tool_call>Read"
            "<arg_key>file_path</arg_key><arg_value>/real.py</arg_value>"
            "</tool_call>"
        )
        buf = _buf()
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Read"

    def test_double_prefix_split_across_chunks(self):
        """Double-prefix where prefix arrives in chunk N, restart in chunk N+1."""
        buf = _buf()
        # Chunk 1: incomplete prefix
        segs1 = buf.feed("<tool_call>G")
        # No tool yet — buffering
        assert not any(s["type"] == "tool_call" for s in segs1)

        # Chunk 2: restart with real name + args
        segs2 = buf.feed(
            "<tool_call>Glob"
            "<arg_key>pattern</arg_key><arg_value>src/**</arg_value>"
            "</tool_call>"
        )
        tools = [s for s in segs2 if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Glob"
        assert tools[0]["input"]["pattern"] == "src/**"

    def test_double_prefix_not_triggered_when_args_before_nested(self):
        """If arg tags appear BEFORE the nested <tool_call, do NOT restart — treat as content."""
        # Unusual case: tool call with <tool_call> inside arg_value (e.g. Write with shell script)
        xml = (
            '<tool_call>Write'
            '<arg_key>file_path</arg_key><arg_value>/a.sh</arg_value>'
            '<arg_key>content</arg_key><arg_value>echo <tool_call>Bash</tool_call></arg_value>'
            '</tool_call>'
        )
        buf = _buf()
        segments = buf.feed(xml)
        # Should emit Write (not restart on the nested <tool_call in arg_value)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Write"


# ═══════════════════════════════════════════════════════════════════════
# 4. XmlToolBuffer.flush() — loose argkv recovery at stream end
# ═══════════════════════════════════════════════════════════════════════

class TestXmlToolBufferFlushLooseArgkv:

    def test_flush_recovers_missing_closing_tag(self):
        """flush() recovers argkv tool when </tool_call> never arrived."""
        xml = "<tool_call>Bash<arg_key>command</arg_key><arg_value>ls -la</arg_value>"
        buf = _buf()
        buf.feed(xml)
        segments = buf.flush()
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1, f"Expected recovered tool, got: {segments}"
        assert tools[0]["name"] == "Bash"
        assert tools[0]["input"]["command"] == "ls -la"

    def test_flush_recovers_multiple_complete_pairs(self):
        """flush() recovers multi-pair argkv without closing tag."""
        xml = (
            "<tool_call>Write"
            "<arg_key>file_path</arg_key><arg_value>/out.py</arg_value>"
            "<arg_key>content</arg_key><arg_value>print('hi')</arg_value>"
        )
        buf = _buf()
        buf.feed(xml)
        segments = buf.flush()
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Write"
        assert tools[0]["input"]["file_path"] == "/out.py"
        assert tools[0]["input"]["content"] == "print('hi')"

    def test_flush_emits_incomplete_when_arg_value_truncated(self):
        """flush() falls back to incomplete_tool_call when arg_value is not closed."""
        xml = "<tool_call>Bash<arg_key>command</arg_key><arg_value>ls"
        buf = _buf()
        buf.feed(xml)
        segments = buf.flush()
        # Cannot recover — must NOT silently discard
        types = [s["type"] for s in segments]
        assert "incomplete_tool_call" in types
        # Must NOT emit a partial tool_call
        assert not any(s["type"] == "tool_call" for s in segments)

    def test_flush_rejects_hallucinated_tool_name(self):
        """flush() does not emit tool_call for hallucinated tool not in valid_names."""
        xml = "<tool_call>NonExistentTool<arg_key>key</arg_key><arg_value>val</arg_value>"
        buf = _buf()
        buf.feed(xml)
        segments = buf.flush()
        # Hallucinated → no tool_call emitted
        assert not any(s["type"] == "tool_call" for s in segments)

    def test_flush_whitespace_name_recovered(self):
        """flush() recovers argkv tool where name has leading/trailing whitespace."""
        xml = "<tool_call>\nBash\n<arg_key>command</arg_key><arg_value>pwd</arg_value>"
        buf = _buf()
        buf.feed(xml)
        segments = buf.flush()
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Bash"
        assert tools[0]["input"]["command"] == "pwd"


# ═══════════════════════════════════════════════════════════════════════
# 5. extract_tool_calls_from_text — 5th fallback (loose argkv)
# ═══════════════════════════════════════════════════════════════════════

class TestExtractToolCallsLooseArgkvFallback:

    def test_loose_argkv_extracted_without_closing_tag(self):
        """Non-streaming: text with argkv but no </tool_call> → tool extracted via 5th fallback."""
        text = "Analysis:\n<tool_call>Bash<arg_key>command</arg_key><arg_value>cat /etc/os-release</arg_value>"
        request = SimpleNamespace(tools=TOOLS)
        blocks, clean = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Bash"
        assert blocks[0]["input"]["command"] == "cat /etc/os-release"

    def test_loose_argkv_not_triggered_when_closing_present(self):
        """4th fallback (strict argkv) should handle complete calls — 5th is not needed."""
        text = "<tool_call>Read<arg_key>file_path</arg_key><arg_value>/a.py</arg_value></tool_call>"
        blocks, clean = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Read"

    def test_loose_argkv_with_whitespace_before_name(self):
        """5th fallback handles whitespace-prefixed name correctly."""
        text = "<tool_call>\n  Glob\n  <arg_key>pattern</arg_key><arg_value>**/*.py</arg_value>"
        blocks, clean = extract_tool_calls_from_text(text, valid_tool_names=VALID_NAMES, tools=TOOLS)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "Glob"


# ═══════════════════════════════════════════════════════════════════════
# 6. strip_tool_call_xml — loose argkv stripped correctly
# ═══════════════════════════════════════════════════════════════════════

class TestStripToolCallXmlLoose:

    def test_strips_complete_argkv(self):
        text = "Prefix <tool_call>Read<arg_key>file_path</arg_key><arg_value>/a.py</arg_value></tool_call> suffix"
        result = strip_tool_call_xml(text)
        assert "<tool_call>" not in result
        assert "<arg_key>" not in result
        assert "<arg_value>" not in result
        assert "Prefix" in result
        assert "suffix" in result

    def test_strips_argkv_without_closing_tag(self):
        """Loose argkv at end of string (no </tool_call>) must be removed."""
        text = "Some text before.\n<tool_call>Bash<arg_key>command</arg_key><arg_value>ls</arg_value>"
        result = strip_tool_call_xml(text)
        assert "<tool_call>" not in result
        assert "<arg_key>" not in result
        assert "<arg_value>" not in result
        assert "Some text before." in result

    def test_strips_loose_argkv_multiline(self):
        text = (
            "Analysis done.\n"
            "<tool_call>\n"
            "Bash\n"
            "<arg_key>command</arg_key>\n"
            "<arg_value>echo hi</arg_value>"
        )
        result = strip_tool_call_xml(text)
        assert "<tool_call>" not in result
        assert "<arg_key>" not in result

    def test_normal_text_unchanged(self):
        text = "No XML here, just plain text."
        assert strip_tool_call_xml(text) == text

    def test_empty_string(self):
        assert strip_tool_call_xml("") == ""

    def test_none_input(self):
        # None is passed through unchanged (guard in strip_tool_call_xml)
        assert strip_tool_call_xml(None) is None


# ═══════════════════════════════════════════════════════════════════════
# 7. XmlToolBuffer — whitespace name in streaming (feed path)
# ═══════════════════════════════════════════════════════════════════════

class TestXmlToolBufferWhitespaceName:

    def test_whitespace_name_in_single_chunk(self):
        """Name with surrounding whitespace from GLM streaming is normalized."""
        xml = "<tool_call> Bash <arg_key>command</arg_key><arg_value>ls</arg_value></tool_call>"
        buf = _buf()
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Bash"

    def test_newline_name_in_stream(self):
        """\\n in name position is stripped."""
        xml = "<tool_call>\nRead\n<arg_key>file_path</arg_key><arg_value>/tmp/x.py</arg_value></tool_call>"
        buf = _buf()
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Read"


# ═══════════════════════════════════════════════════════════════════════
# 8. Dotted/dashed tool names end-to-end through buffer
# ═══════════════════════════════════════════════════════════════════════

class TestExtendedToolNames:

    def test_dotted_name_via_argkv_re(self):
        """computer.bash is matched by [\\w.-]+."""
        xml = "<tool_call>computer.bash<arg_key>command</arg_key><arg_value>pwd</arg_value></tool_call>"
        m = _TOOL_CALL_ARGKV_RE.search(xml)
        assert m is not None
        assert m.group(1) == "computer.bash"

    def test_dotted_name_in_loose_re(self):
        xml = "<tool_call>computer.bash<arg_key>command</arg_key><arg_value>pwd</arg_value>"
        m = _TOOL_CALL_ARGKV_LOOSE_RE.search(xml)
        assert m is not None
        assert m.group(1) == "computer.bash"

    def test_dotted_name_through_extract(self):
        """extract_tool_calls_from_text handles dotted name in valid_tool_names."""
        dotted_tools = [{"name": "computer.bash", "description": "...", "input_schema": {
            "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"],
        }}]
        valid = _build_valid_tool_names(dotted_tools)
        text = "<tool_call>computer.bash<arg_key>command</arg_key><arg_value>ls</arg_value></tool_call>"
        blocks, _ = extract_tool_calls_from_text(text, valid_tool_names=valid, tools=dotted_tools)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "computer.bash"


# ═══════════════════════════════════════════════════════════════════════
# 9. Edge cases — no crash guarantees
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCasesNoCrash:

    def test_empty_tool_call_tag(self):
        """<tool_call></tool_call> — no crash."""
        buf = _buf()
        buf.feed("<tool_call></tool_call>")
        buf.flush()  # must not raise

    def test_tool_call_open_only(self):
        """<tool_call — stream ends without anything else."""
        buf = _buf()
        buf.feed("<tool_call")
        segs = buf.flush()
        assert isinstance(segs, list)

    def test_arg_tags_without_tool_call(self):
        """Orphaned <arg_key>/<arg_value> outside a <tool_call — no crash."""
        buf = _buf()
        segs = buf.feed("<arg_key>command</arg_key><arg_value>ls</arg_value>")
        # Must not raise, must emit text (not tool_call)
        assert isinstance(segs, list)
        assert not any(s["type"] == "tool_call" for s in segs)

    def test_double_prefix_no_args_ever(self):
        """Double-prefix where second <tool_call also has no args — no infinite loop."""
        buf = _buf()
        # Feed a pathological case: two open tags, no args, no close
        segs1 = buf.feed("<tool_call>A<tool_call>B")
        segs2 = buf.flush()
        # Must not crash or loop forever
        assert isinstance(segs1, list)
        assert isinstance(segs2, list)

    def test_nested_xml_inside_arg_value(self):
        """arg_value containing < and > chars is preserved intact."""
        xml = (
            "<tool_call>Write"
            "<arg_key>content</arg_key>"
            "<arg_value><html><body>hello</body></html></arg_value>"
            "</tool_call>"
        )
        buf = _buf()
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert "<html>" in tools[0]["input"]["content"]


# ═══════════════════════════════════════════════════════════════════════
# 10. Production hardening fixes
# ═══════════════════════════════════════════════════════════════════════

class TestProductionHardeningFixes:

    def test_double_prefix_no_false_positive_on_longer_tag_in_arg_value(self):
        """`<tool_call_backup>` inside arg_value must NOT trigger double-prefix restart.

        The substring `<tool_call` appears inside `<tool_call_backup>` in the arg_value.
        The has_args guard (arg tags appear before the nested `<tool_call`) should
        prevent the restart. After hardening, the after-char validation also blocks it
        since `_` follows `<tool_call` in `<tool_call_backup>`.
        """
        xml = (
            "<tool_call>Write"
            "<arg_key>content</arg_key>"
            "<arg_value>See <tool_call_backup> tag for reference</arg_value>"
            "</tool_call>"
        )
        buf = _buf()
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "Write"
        assert "<tool_call_backup>" in tools[0]["input"]["content"]

    def test_underscore_prefix_tool_name_enters_tool_mode(self):
        """Tool name starting with `_` must be detected by _try_extract_text().

        MCP tools or custom tools may use _underscore_prefix naming.
        Before the fix, isalpha() rejected `_` and the tag was emitted as text.
        """
        underscore_tools = [{"name": "_internal", "description": "x", "input_schema": {
            "type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"],
        }}]
        valid = _build_valid_tool_names(underscore_tools)
        buf = XmlToolBuffer(valid_tool_names=valid, tools=underscore_tools)
        xml = "<tool_call>_internal<arg_key>key</arg_key><arg_value>val</arg_value></tool_call>"
        segments = buf.feed(xml)
        tools = [s for s in segments if s["type"] == "tool_call"]
        assert len(tools) == 1
        assert tools[0]["name"] == "_internal"
        assert tools[0]["input"]["key"] == "val"

    def test_diluted_re_mismatched_tags_match_intentionally(self):
        """`_TOOL_DILUTED_RE` allows `<args>...</arguments>` (documented behavior).

        Opening and closing alternations are not backreferenced, so mismatched tags
        still extract content. This is intentional — content extraction > strict tags.
        """
        from llm.tool_prompting import _TOOL_DILUTED_RE
        text = "<tool_name>Bash</tool_name><args>ls -la</arguments>"
        m = _TOOL_DILUTED_RE.search(text)
        assert m is not None, "Mismatched diluted tags should still match (intentional)"
        assert m.group(1) == "Bash"
        assert "ls -la" in m.group(2)

    def test_recover_incomplete_tool_call_has_tools_parameter(self):
        """recover_incomplete_tool_call must accept `tools` parameter for valid_tool_names."""
        import inspect
        from llm.tool_prompting import recover_incomplete_tool_call
        sig = inspect.signature(recover_incomplete_tool_call)
        assert "tools" in sig.parameters, (
            "tools parameter required — line 1154 fix relies on it for hallucination filtering"
        )

    def test_large_text_without_tool_call_drains_as_text(self):
        """Large plain text (> _MAX_TOOL_BUFFER) with no <tool_call> drains normally.

        _try_extract_text() always drains text when there's no plausible <tool_call.
        The buffer does NOT grow unboundedly when in_tool=False — a partial <tool_call
        at the buffer end is at most len("<tool_call")-1 = 9 chars.
        """
        buf = XmlToolBuffer(valid_tool_names=VALID_NAMES, tools=TOOLS)
        large_text = "x" * (buf._MAX_TOOL_BUFFER + 1)
        segments = buf.feed(large_text)
        text_segs = [s for s in segments if s["type"] == "text"]
        assert len(text_segs) == 1, "Large plain text should drain as a single text segment"
        assert len(text_segs[0]["text"]) == buf._MAX_TOOL_BUFFER + 1
