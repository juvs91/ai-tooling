# tests/test_no_progress_rule.py
"""Unit tests for llm/state_assertion_rules/no_progress.py (ADR-0039).

These tests exercise the RULE's adaptation layer, not
guardrail.py's detection logic itself (already covered by that module's own
tests) — the point of this rule is that it reuses, not reimplements,
_detect_consistently_failing_tools/_detect_stuck_tool_calls.
"""
import pytest

from llm.state_assertion_rules.no_progress import NoProgressRule


def _asst(tool_use_id, name, input_=None):
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": input_ or {}}],
    }


def _tool_result(tool_use_id, content=None, is_error=False):
    return {
        "role": "user",
        "content": [{
            "type": "tool_result", "tool_use_id": tool_use_id,
            "content": content, "is_error": is_error,
        }],
    }


def _evaluate(messages):
    rule = NoProgressRule()
    return rule.evaluate(
        content=None, messages=messages, ctx=None, tools=None,
        model="kimi-k2", session_snapshot={},
    )


class TestFailureDrivenDetection:
    def test_two_consecutive_errors_produce_one_finding(self):
        # Distinct inputs per call so _detect_stuck_tool_calls (identical-input
        # loop) doesn't ALSO fire — this test isolates the failing-tool path.
        messages = [
            _asst("t1", "mcp__playwright__playwright_get", {"attempt": 1}),
            _tool_result("t1", content="[ERROR] connection refused", is_error=True),
            _asst("t2", "mcp__playwright__playwright_get", {"attempt": 2}),
            _tool_result("t2", content="[ERROR] connection refused", is_error=True),
        ]
        findings = _evaluate(messages)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "no_progress"
        assert f.subject == "mcp__playwright__playwright_get"
        assert f.severity == "log"
        assert f.verdict == "contradicted"
        assert "2 time(s)" in f.correction_note

    def test_single_error_does_not_fire(self):
        messages = [
            _asst("t1", "Read"),
            _tool_result("t1", content="[ERROR] file not found", is_error=True),
        ]
        assert _evaluate(messages) == []

    def test_successful_calls_do_not_fire(self):
        messages = [
            _asst("t1", "Read", {"file_path": "a.py"}),
            _tool_result("t1", content="file contents"),
            _asst("t2", "Read", {"file_path": "b.py"}),
            _tool_result("t2", content="more contents"),
        ]
        assert _evaluate(messages) == []

    def test_empty_messages_returns_empty(self):
        assert _evaluate([]) == []


class TestStuckLoopDetection:
    def test_identical_input_repeated_produces_finding(self):
        messages = [
            _asst("t1", "mcp__weird__list", {"path": "/"}),
            _tool_result("t1", content="Directory listing is not enabled in this build."),
            _asst("t2", "mcp__weird__list", {"path": "/"}),
            _tool_result("t2", content="Directory listing is not enabled in this build."),
        ]
        findings = _evaluate(messages)
        assert len(findings) == 1
        assert findings[0].subject == "mcp__weird__list"
        assert findings[0].severity == "log"

    def test_different_inputs_do_not_fire_stuck_loop(self):
        messages = [
            _asst("t1", "mcp__api__page", {"page": 1}),
            _tool_result("t1", content="page 1 data"),
            _asst("t2", "mcp__api__page", {"page": 2}),
            _tool_result("t2", content="page 2 data"),
        ]
        assert _evaluate(messages) == []


class TestBothDetectionsCanFireTogether:
    def test_failing_tool_and_stuck_tool_both_reported(self):
        messages = [
            # Failing tool (distinct inputs so stuck-loop doesn't also match it)
            _asst("t1", "mcp__playwright__playwright_get", {"attempt": 1}),
            _tool_result("t1", content="[ERROR] refused", is_error=True),
            _asst("t2", "mcp__playwright__playwright_get", {"attempt": 2}),
            _tool_result("t2", content="[ERROR] refused", is_error=True),
            # Stuck tool (different name, no errors, identical input)
            _asst("t3", "mcp__weird__list", {"path": "/"}),
            _tool_result("t3", content="empty"),
            _asst("t4", "mcp__weird__list", {"path": "/"}),
            _tool_result("t4", content="empty"),
        ]
        findings = _evaluate(messages)
        subjects = {f.subject for f in findings}
        assert subjects == {"mcp__playwright__playwright_get", "mcp__weird__list"}


class TestRuleMetadata:
    def test_rule_is_agnostic_request_phase(self):
        rule = NoProgressRule()
        assert rule.agnostic is True
        assert rule.phase == "request"
        assert rule.id == "no_progress"
