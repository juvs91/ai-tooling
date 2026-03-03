# tests/test_safe_split.py
"""Tests for Fix 1 (_find_safe_split_point) and Fix 2 (_validate_tool_references, _fix_orphan_tool_messages)."""
import pytest

from llm.compressor import (
    _find_safe_split_point,
    _validate_tool_references,
    _fix_orphan_tool_messages,
    _reassemble_with_summary,
    _reassemble_trimmed,
)


# ── Fix 1: _find_safe_split_point ──────────────────────────────────────


class TestFindSafeSplitPoint:

    def test_no_tool_messages_returns_naive_split(self):
        """When no role:tool in recent, split = len - keep_recent."""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "how are you"},
            {"role": "assistant", "content": "good"},
            {"role": "user", "content": "bye"},
        ]
        assert _find_safe_split_point(msgs, keep_recent=2) == 3

    def test_conversation_shorter_than_keep_recent(self):
        """When conversation <= keep_recent, return 0 (keep all)."""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert _find_safe_split_point(msgs, keep_recent=5) == 0

    def test_tool_result_with_parent_in_old_moves_split_back(self):
        """The critical bug: tool_result in recent, parent assistant in old."""
        msgs = [
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": "m2",
             "tool_calls": [{"id": "toolu_abc", "function": {"name": "Read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "toolu_abc", "content": "file content"},
            {"role": "assistant", "content": "m4"},
            {"role": "user", "content": "m5"},
        ]
        # keep_recent=3 → naive split at index 2, recent = [tool(abc), m4, m5]
        # tool(abc) references toolu_abc in msg[1] → split should move to 1
        result = _find_safe_split_point(msgs, keep_recent=3)
        assert result == 1

    def test_multiple_orphan_references_all_resolved(self):
        """Multiple tool_results referencing different assistant messages."""
        msgs = [
            {"role": "user", "content": "m0"},
            {"role": "assistant", "content": "m1",
             "tool_calls": [{"id": "toolu_1", "function": {"name": "Read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "toolu_1", "content": "r1"},
            {"role": "assistant", "content": "m3",
             "tool_calls": [{"id": "toolu_2", "function": {"name": "Write", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "toolu_2", "content": "r2"},
            {"role": "assistant", "content": "m5"},
            {"role": "user", "content": "m6"},
        ]
        # keep_recent=3 → naive split at 4, recent = [tool(2), m5, m6]
        # tool(2) references toolu_2 at msg[3] → split should move to 3
        result = _find_safe_split_point(msgs, keep_recent=3)
        assert result == 3

    def test_parent_already_in_recent_no_change(self):
        """tool_result and its parent both in recent → no adjustment needed."""
        msgs = [
            {"role": "user", "content": "m0"},
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": "m2",
             "tool_calls": [{"id": "toolu_x", "function": {"name": "Read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "toolu_x", "content": "result"},
            {"role": "user", "content": "m4"},
        ]
        # keep_recent=3 → naive split at 2, recent = [assistant(toolu_x), tool(x), m4]
        # Parent is at index 2 which IS in recent → no change
        result = _find_safe_split_point(msgs, keep_recent=3)
        assert result == 2

    def test_tool_calls_as_objects_not_dicts(self):
        """Handle tool_calls stored as objects with .id attribute."""
        class ToolCall:
            def __init__(self, id):
                self.id = id
        msgs = [
            {"role": "user", "content": "m0"},
            {"role": "assistant", "content": "m1",
             "tool_calls": [ToolCall("toolu_obj")]},
            {"role": "tool", "tool_call_id": "toolu_obj", "content": "r"},
            {"role": "user", "content": "m3"},
        ]
        # keep_recent=2 → naive split at 2, recent = [tool(obj), m3]
        # tool references toolu_obj at msg[1] → split to 1
        result = _find_safe_split_point(msgs, keep_recent=2)
        assert result == 1

    def test_no_tools_model_no_tool_messages(self):
        """For no-tools models, there are no role:tool messages at all."""
        msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": "I used <tool_call>Read</tool_call>"},
            {"role": "user", "content": "<tool_result>content</tool_result>"},
            {"role": "assistant", "content": "done"},
        ]
        # No role:"tool" messages → naive split
        result = _find_safe_split_point(msgs, keep_recent=2)
        assert result == 3

    def test_deeply_nested_orphan_requires_big_shift(self):
        """Parent assistant is far back from the split point."""
        msgs = [
            {"role": "user", "content": "m0"},
            {"role": "assistant", "content": "m1",
             "tool_calls": [{"id": "toolu_deep", "function": {"name": "Read", "arguments": "{}"}}]},
            {"role": "user", "content": "m2"},
            {"role": "user", "content": "m3"},
            {"role": "user", "content": "m4"},
            {"role": "user", "content": "m5"},
            {"role": "tool", "tool_call_id": "toolu_deep", "content": "r"},
            {"role": "user", "content": "m7"},
        ]
        # keep_recent=2 → naive split at 6, recent = [tool(deep), m7]
        # Parent at msg[1] → split moves to 1
        result = _find_safe_split_point(msgs, keep_recent=2)
        assert result == 1


# ── Fix 2: _validate_tool_references ───────────────────────────────────


class TestValidateToolReferences:

    def test_valid_references_return_true(self):
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "toolu_1", "function": {"name": "Read"}}]},
            {"role": "tool", "tool_call_id": "toolu_1", "content": "ok"},
        ]
        assert _validate_tool_references(msgs) is True

    def test_orphan_reference_returns_false(self):
        msgs = [
            {"role": "user", "content": "summary"},
            {"role": "assistant", "content": "understood"},
            {"role": "tool", "tool_call_id": "toolu_missing", "content": "data"},
        ]
        assert _validate_tool_references(msgs) is False

    def test_unknown_tool_call_id_is_ignored(self):
        """tool_call_id='unknown' is a sentinel used by the proxy, should not fail."""
        msgs = [
            {"role": "tool", "tool_call_id": "unknown", "content": "data"},
        ]
        assert _validate_tool_references(msgs) is True

    def test_no_tool_messages_is_valid(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert _validate_tool_references(msgs) is True

    def test_empty_messages_is_valid(self):
        assert _validate_tool_references([]) is True

    def test_multiple_valid_references(self):
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [
                 {"id": "t1", "function": {"name": "Read"}},
                 {"id": "t2", "function": {"name": "Write"}},
             ]},
            {"role": "tool", "tool_call_id": "t1", "content": "r1"},
            {"role": "tool", "tool_call_id": "t2", "content": "r2"},
        ]
        assert _validate_tool_references(msgs) is True

    def test_partial_orphan_returns_false(self):
        """One valid, one orphan → False."""
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "t1", "function": {"name": "Read"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "r1"},
            {"role": "tool", "tool_call_id": "t_orphan", "content": "r2"},
        ]
        assert _validate_tool_references(msgs) is False


# ── Fix 2: _fix_orphan_tool_messages ───────────────────────────────────


class TestFixOrphanToolMessages:

    def test_converts_orphan_to_user_message(self):
        msgs = [
            {"role": "user", "content": "summary"},
            {"role": "assistant", "content": "ok"},
            {"role": "tool", "tool_call_id": "toolu_orphan", "content": "file data"},
        ]
        result = _fix_orphan_tool_messages(msgs)
        assert len(result) == 3
        assert result[2]["role"] == "user"
        assert "toolu_orphan" in result[2]["content"]
        assert "file data" in result[2]["content"]

    def test_preserves_valid_tool_messages(self):
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "t1", "function": {"name": "Read"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "ok"},
        ]
        result = _fix_orphan_tool_messages(msgs)
        assert result == msgs

    def test_mixed_valid_and_orphan(self):
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "t1", "function": {"name": "Read"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "ok"},
            {"role": "tool", "tool_call_id": "t_orphan", "content": "orphan data"},
        ]
        result = _fix_orphan_tool_messages(msgs)
        assert len(result) == 3
        assert result[1]["role"] == "tool"  # valid one preserved
        assert result[2]["role"] == "user"  # orphan converted

    def test_unknown_tool_call_id_preserved(self):
        """tool_call_id='unknown' should NOT be converted."""
        msgs = [
            {"role": "tool", "tool_call_id": "unknown", "content": "data"},
        ]
        result = _fix_orphan_tool_messages(msgs)
        assert result[0]["role"] == "tool"


# ── Integration: _reassemble_with_summary validation ───────────────────


class TestReassembleValidation:

    def test_reassemble_with_summary_fixes_orphans(self):
        """_reassemble_with_summary should fix orphan tool references."""
        recent = [
            {"role": "tool", "tool_call_id": "toolu_orphan", "content": "data"},
            {"role": "user", "content": "next"},
        ]
        result = _reassemble_with_summary(None, "summary text", recent)
        # The orphan tool should be converted to user message
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 0

    def test_reassemble_trimmed_fixes_orphans(self):
        """_reassemble_trimmed should fix orphan tool references."""
        recent = [
            {"role": "tool", "tool_call_id": "toolu_orphan", "content": "data"},
            {"role": "user", "content": "next"},
        ]
        result = _reassemble_trimmed(None, recent)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 0

    def test_reassemble_preserves_valid_tool_pairs(self):
        """Valid tool_call_id pairs should be preserved through reassembly."""
        recent = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "t1", "function": {"name": "Read"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "data"},
            {"role": "user", "content": "next"},
        ]
        result = _reassemble_with_summary(None, "summary", recent)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "t1"
