# tests/test_state_assertion.py
"""Unit tests for llm/state_assertion.py — the core engine (ADR-0036).

`rules` is an explicit parameter of evaluate_rules() (no module-level
registry — see llm/state_assertion_rules/__init__.py's docstring for why),
so every test builds its own local list of synthetic dummy rules and passes
it directly. No shared state, no fixture needed to isolate tests from
each other.
"""
import pytest

from llm.state_assertion import AssertionFinding, evaluate_rules


class _DummyRule:
    """Minimal StateAssertionRule for engine tests."""

    def __init__(self, id_, phase, agnostic, findings=None):
        self.id = id_
        self.phase = phase
        self.agnostic = agnostic
        self._findings = findings if findings is not None else [
            AssertionFinding(
                rule_id=id_, verdict="contradicted", evidence_snippet="x",
                subject="X", correction_note="note", severity="nudge",
            )
        ]
        self.call_count = 0

    def evaluate(self, **kwargs):
        self.call_count += 1
        return list(self._findings)


class TestEvaluateRulesEmptyRules:
    def test_empty_rules_returns_no_findings(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        findings = evaluate_rules(
            "request", [], content=None, messages=[], ctx=None, tools=None,
            model="claude-sonnet-5", session_snapshot={},
        )
        assert findings == []


class TestEvaluateRulesPhaseFiltering:
    def test_only_matching_phase_runs(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        req_rule = _DummyRule("req_rule", "request", agnostic=True)
        resp_rule = _DummyRule("resp_rule", "response", agnostic=True)

        findings = evaluate_rules(
            "request", [req_rule, resp_rule], content=None, messages=[], ctx=None,
            tools=None, model="claude-sonnet-5", session_snapshot={},
        )
        assert req_rule.call_count == 1
        assert resp_rule.call_count == 0
        assert [f.rule_id for f in findings] == ["req_rule"]

    def test_response_phase_runs_response_rules_only(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        req_rule = _DummyRule("req_rule", "request", agnostic=True)
        resp_rule = _DummyRule("resp_rule", "response", agnostic=True)

        findings = evaluate_rules(
            "response", [req_rule, resp_rule], content=None, messages=[], ctx=None,
            tools=None, model="claude-sonnet-5", session_snapshot={},
        )
        assert req_rule.call_count == 0
        assert resp_rule.call_count == 1
        assert [f.rule_id for f in findings] == ["resp_rule"]


class TestEvaluateRulesAgnosticGating:
    def test_agnostic_rule_runs_for_any_model(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        rule = _DummyRule("agnostic_rule", "request", agnostic=True)

        findings = evaluate_rules(
            "request", [rule], content=None, messages=[], ctx=None, tools=None,
            model="claude-sonnet-5", session_snapshot={},
        )
        assert rule.call_count == 1
        assert len(findings) == 1

    def test_non_agnostic_rule_skipped_for_non_fragile_model(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        rule = _DummyRule("fragile_only_rule", "request", agnostic=False)

        findings = evaluate_rules(
            "request", [rule], content=None, messages=[], ctx=None, tools=None,
            model="claude-sonnet-5", session_snapshot={},
        )
        assert rule.call_count == 0
        assert findings == []

    def test_non_agnostic_rule_runs_for_fragile_model(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: True
        )
        rule = _DummyRule("fragile_only_rule", "request", agnostic=False)

        findings = evaluate_rules(
            "request", [rule], content=None, messages=[], ctx=None, tools=None,
            model="kimi-k2", session_snapshot={},
        )
        assert rule.call_count == 1
        assert len(findings) == 1


class TestEvaluateRulesAggregation:
    def test_multiple_matching_rules_all_contribute(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        rule_a = _DummyRule("rule_a", "response", agnostic=True)
        rule_b = _DummyRule("rule_b", "response", agnostic=True)

        findings = evaluate_rules(
            "response", [rule_a, rule_b], content=None, messages=[], ctx=None,
            tools=None, model="claude-sonnet-5", session_snapshot={},
        )
        assert {f.rule_id for f in findings} == {"rule_a", "rule_b"}

    def test_rule_returning_empty_list_contributes_nothing(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        rule = _DummyRule("quiet_rule", "response", agnostic=True, findings=[])

        findings = evaluate_rules(
            "response", [rule], content=None, messages=[], ctx=None, tools=None,
            model="claude-sonnet-5", session_snapshot={},
        )
        assert rule.call_count == 1
        assert findings == []


class _RaisingRule:
    """Simulates a buggy rule (regex crash, missing dict key, unexpected
    content shape, etc.) to verify evaluate_rules() isolates it."""

    def __init__(self, id_, phase, agnostic=True, exc=None):
        self.id = id_
        self.phase = phase
        self.agnostic = agnostic
        self._exc = exc or RuntimeError("boom")

    def evaluate(self, **kwargs):
        raise self._exc


class TestEvaluateRulesFaultIsolation:
    """A raising rule must never crash evaluate_rules() itself, nor prevent
    other rules from running — see the safety rationale in evaluate_rules()'s
    docstring: Pipeline.process() re-raises, and the non-streaming response
    path has no fallback, so an unhandled exception here would turn an
    already-generated model response into a lost turn + HTTP error."""

    def test_raising_rule_does_not_propagate(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        # Must not raise.
        findings = evaluate_rules(
            "response", [_RaisingRule("buggy_rule", "response")],
            content=None, messages=[], ctx=None, tools=None,
            model="claude-sonnet-5", session_snapshot={},
        )
        assert findings == []

    def test_raising_rule_does_not_block_other_rules(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        good_rule = _DummyRule("good_rule", "response", agnostic=True)

        findings = evaluate_rules(
            "response", [_RaisingRule("buggy_rule", "response"), good_rule],
            content=None, messages=[], ctx=None, tools=None,
            model="claude-sonnet-5", session_snapshot={},
        )
        assert [f.rule_id for f in findings] == ["good_rule"]

    def test_raising_rule_order_independent(self, monkeypatch):
        """Same guarantee regardless of whether the buggy rule runs first or last."""
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        good_rule = _DummyRule("good_rule", "response", agnostic=True)

        findings = evaluate_rules(
            "response", [good_rule, _RaisingRule("buggy_rule", "response")],
            content=None, messages=[], ctx=None, tools=None,
            model="claude-sonnet-5", session_snapshot={},
        )
        assert [f.rule_id for f in findings] == ["good_rule"]

    def test_different_exception_types_are_all_isolated(self, monkeypatch):
        monkeypatch.setattr(
            "llm.state_assertion.is_fragile_orchestration_model", lambda m: False
        )
        for exc in (KeyError("missing"), AttributeError("no attr"), TypeError("bad type"),
                    ValueError("bad value")):
            findings = evaluate_rules(
                "response", [_RaisingRule("buggy_rule", "response", exc=exc)],
                content=None, messages=[], ctx=None, tools=None,
                model="claude-sonnet-5", session_snapshot={},
            )
            assert findings == []


class TestAssertionFindingImmutable:
    def test_finding_is_frozen(self):
        finding = AssertionFinding(
            rule_id="r", verdict="contradicted", evidence_snippet="e",
            subject="s", correction_note="c", severity="nudge",
        )
        with pytest.raises(Exception):
            finding.rule_id = "other"
