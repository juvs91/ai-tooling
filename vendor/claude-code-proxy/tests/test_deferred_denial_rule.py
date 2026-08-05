# tests/test_deferred_denial_rule.py
"""Unit tests for llm/state_assertion_rules/deferred_denial.py (ADR-0038).

Importing this module has zero side effects — there is no rule registry to
populate. Rule construction is explicit now
(llm/state_assertion_rules/__init__.py's build_active_rules()), so these
tests exercise DeferredDenialRule directly, on a fresh instance each time.
"""
import pytest

from llm.state_assertion_rules.deferred_denial import DeferredDenialRule


def _content(text):
    return [{"type": "text", "text": text}]


def _evaluate(text, deferred_tool_names=None):
    rule = DeferredDenialRule()
    return rule.evaluate(
        content=_content(text),
        messages=[],
        ctx=None,
        tools=None,
        model="kimi-k2",
        session_snapshot={"deferred_tool_names": deferred_tool_names or []},
    )


class TestKnownStaticCatalogDenials:
    def test_denies_enter_plan_mode(self):
        """Reconstructed from bad_conversation.txt lines 7822-7846: 'There's an
        EnterPlanMode tool? Not listed... I don't have EnterPlanMode tool'."""
        findings = _evaluate(
            "There's an EnterPlanMode tool? Not listed. I don't have EnterPlanMode tool."
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "deferred_denial"
        assert f.subject == "EnterPlanMode"
        assert f.verdict == "contradicted"
        assert "ToolSearch('select:EnterPlanMode')" in f.correction_note

    def test_denies_ask_user_question(self):
        findings = _evaluate("I don't have AskUserQuestion available in my tool list.")
        assert len(findings) == 1
        assert findings[0].subject == "AskUserQuestion"

    def test_spanish_denial_phrase(self):
        findings = _evaluate("No tengo la tool WebFetch disponible en este momento.")
        assert len(findings) == 1
        assert findings[0].subject == "WebFetch"

    def test_spanish_not_connected_phrase(self):
        findings = _evaluate("La tool ToolSearch no está conectada en esta sesión.")
        assert len(findings) == 1
        assert findings[0].subject == "ToolSearch"


class TestSessionCachedMcpDenials:
    def test_denies_known_session_mcp_tool(self):
        """Reconstructed from bad_conversation.txt lines 400-3242: repeated
        confusion over mcp__playwright__playwright_get."""
        findings = _evaluate(
            "It seems mcp__playwright__playwright_get isn't connected, so I'll use "
            "BigQuery instead.",
            deferred_tool_names=["mcp__playwright__playwright_get"],
        )
        assert len(findings) == 1
        assert findings[0].subject == "mcp__playwright__playwright_get"

    def test_mcp_tool_recognized_via_regex_even_without_session_cache(self):
        """mcp__ pattern is validated structurally (_MCP_TOOL_RE), not only via
        the session cache — matches validate_tool_name_with_deferred_bypass's
        own logic in utils/tool_utils.py."""
        findings = _evaluate(
            "I don't have mcp__serper__google_search available.",
            deferred_tool_names=[],
        )
        assert len(findings) == 1
        assert findings[0].subject == "mcp__serper__google_search"


class TestNoFalsePositives:
    def test_unknown_tool_name_does_not_fire(self):
        """A model correctly stating an unknown/nonexistent tool doesn't exist
        must NOT be flagged — the rule only contradicts provably-false denials."""
        findings = _evaluate("I don't have a tool called FooBarBazQuux.")
        assert findings == []

    def test_mention_without_denial_phrase_does_not_fire(self):
        findings = _evaluate("I already used WebFetch earlier to read the tutorial.")
        assert findings == []

    def test_denial_phrase_without_tool_name_does_not_fire(self):
        findings = _evaluate("I don't have access to that right now.")
        assert findings == []

    def test_empty_content_returns_empty(self):
        rule = DeferredDenialRule()
        assert rule.evaluate(
            content=None, messages=[], ctx=None, tools=None,
            model="kimi-k2", session_snapshot={},
        ) == []
        assert rule.evaluate(
            content=[{"type": "tool_use", "name": "Read", "input": {}}],
            messages=[], ctx=None, tools=None, model="kimi-k2",
            session_snapshot={},
        ) == []

    def test_missing_session_snapshot_key_does_not_crash(self):
        """session_snapshot without 'deferred_tool_names' at all (e.g. called
        from a hypothetical request-phase context) must not raise."""
        rule = DeferredDenialRule()
        findings = rule.evaluate(
            content=_content("I don't have EnterPlanMode tool."),
            messages=[], ctx=None, tools=None, model="kimi-k2",
            session_snapshot={},
        )
        assert len(findings) == 1  # EnterPlanMode still matches the static catalog


class TestMultipleDenialsInOneResponse:
    def test_two_distinct_tools_each_produce_a_finding(self):
        findings = _evaluate(
            "I don't have EnterPlanMode tool. Also, ExitPlanMode isn't available either."
        )
        subjects = {f.subject for f in findings}
        assert subjects == {"EnterPlanMode", "ExitPlanMode"}

    def test_same_tool_denied_twice_is_deduplicated(self):
        findings = _evaluate(
            "I don't have EnterPlanMode tool. I really don't have EnterPlanMode tool."
        )
        assert len(findings) == 1
        assert findings[0].subject == "EnterPlanMode"


class TestSeverityAndAgnostic:
    def test_severity_is_nudge(self):
        findings = _evaluate("I don't have EnterPlanMode tool.")
        assert findings[0].severity == "nudge"

    def test_rule_is_agnostic_and_response_phase(self):
        rule = DeferredDenialRule()
        assert rule.agnostic is True
        assert rule.phase == "response"
        assert rule.id == "deferred_denial"
