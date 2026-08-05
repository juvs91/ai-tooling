# tests/test_state_assertion_transformers.py
"""Integration tests for StateAssertionRequestTransformer and
StateAssertionResponseTransformer (ADR-0036).

Rules are injected explicitly via each transformer's constructor (no
module-level registry — see llm/state_assertion_rules/__init__.py's
docstring), so each test constructs its own synthetic dummy rule(s) and
passes them directly, verifying the pipeline wiring: system-note injection
(request phase), ctx.grounding_issues population (response phase),
ctx.state_assertion_findings bookkeeping, and session-event persistence —
without depending on any real rule's regex logic.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from llm.pipeline import TransformContext
from llm.state_assertion import AssertionFinding
from llm.transformers.state_assertion_request import StateAssertionRequestTransformer
from llm.transformers.state_assertion_response import StateAssertionResponseTransformer


class _DummyRule:
    def __init__(self, id_, phase, agnostic=True, findings=None):
        self.id = id_
        self.phase = phase
        self.agnostic = agnostic
        self._findings = findings if findings is not None else [
            AssertionFinding(
                rule_id=id_, verdict="contradicted", evidence_snippet="ev",
                subject="EnterPlanMode", correction_note="use ToolSearch",
                severity="nudge",
            )
        ]

    def evaluate(self, **kwargs):
        return list(self._findings)


class _MockRequest:
    def __init__(self, model="claude-sonnet-5", system=None, messages=None,
                 tools=None, content=None):
        self.model = model
        self.system = system
        self.messages = messages or []
        self.tools = tools or []
        self.content = content


def _make_ctx(session_id="ss-test"):
    ctx = TransformContext()
    ctx.session_id = session_id
    return ctx


class TestStateAssertionRequestTransformer:
    @pytest.mark.asyncio
    async def test_no_findings_is_noop(self):
        transformer = StateAssertionRequestTransformer(rules=[])
        req = _MockRequest(system="You are helpful.")
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_request.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]):
            await transformer.transform(req, ctx)

        assert req.system == "You are helpful."
        assert ctx.state_assertion_findings == []

    @pytest.mark.asyncio
    async def test_finding_injects_system_note_on_request_not_ctx(self):
        transformer = StateAssertionRequestTransformer(
            rules=[_DummyRule("dummy_request_rule", "request")]
        )
        req = _MockRequest(system=None)
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_request.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_request.append_session_assertion_event",
                   new_callable=AsyncMock):
            await transformer.transform(req, ctx)

        assert req.system is not None
        assert "use ToolSearch" in req.system
        assert not hasattr(ctx, "system")  # never written to ctx (the no-op bug)

    @pytest.mark.asyncio
    async def test_finding_recorded_in_ctx_state_assertion_findings(self):
        transformer = StateAssertionRequestTransformer(
            rules=[_DummyRule("dummy_request_rule", "request")]
        )
        req = _MockRequest(system=None)
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_request.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_request.append_session_assertion_event",
                   new_callable=AsyncMock):
            await transformer.transform(req, ctx)

        assert len(ctx.state_assertion_findings) == 1
        entry = ctx.state_assertion_findings[0]
        assert entry["rule_id"] == "dummy_request_rule"
        assert entry["subject"] == "EnterPlanMode"
        assert entry["severity"] == "nudge"

    @pytest.mark.asyncio
    async def test_finding_schedules_session_persistence(self):
        transformer = StateAssertionRequestTransformer(
            rules=[_DummyRule("dummy_request_rule", "request")]
        )
        req = _MockRequest(system=None)
        ctx = _make_ctx(session_id="persist-me")

        with patch("llm.transformers.state_assertion_request.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_request.append_session_assertion_event",
                   new_callable=AsyncMock) as mock_append:
            await transformer.transform(req, ctx)
            # asyncio.create_task schedules but doesn't await — give the loop a tick
            import asyncio
            await asyncio.sleep(0)

        mock_append.assert_called_once()
        args = mock_append.call_args.args
        assert args[0] == "persist-me"
        assert args[1] == "dummy_request_rule"

    @pytest.mark.asyncio
    async def test_no_session_id_skips_persistence_but_still_nudges(self):
        transformer = StateAssertionRequestTransformer(
            rules=[_DummyRule("dummy_request_rule", "request")]
        )
        req = _MockRequest(system=None)
        ctx = _make_ctx(session_id="")

        with patch("llm.transformers.state_assertion_request.get_session_assertion_events",
                   new_callable=AsyncMock) as mock_get, \
             patch("llm.transformers.state_assertion_request.append_session_assertion_event",
                   new_callable=AsyncMock) as mock_append:
            await transformer.transform(req, ctx)

        mock_get.assert_not_called()
        mock_append.assert_not_called()
        assert "use ToolSearch" in req.system


class TestStateAssertionResponseTransformer:
    @pytest.mark.asyncio
    async def test_no_findings_is_noop(self):
        transformer = StateAssertionResponseTransformer(rules=[])
        req = _MockRequest(content=[{"type": "text", "text": "hi"}])
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_response.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]):
            await transformer.transform(req, ctx)

        assert ctx.grounding_issues == []
        assert ctx.state_assertion_findings == []

    @pytest.mark.asyncio
    async def test_finding_appends_to_grounding_issues_not_ensure_system_note(self):
        """Response-phase findings must go through ctx.grounding_issues (the real
        channel quality_refinement.py consumes) — NOT ensure_system_note(ctx, ...),
        which is a documented no-op (TransformContext has no `system` attr)."""
        transformer = StateAssertionResponseTransformer(
            rules=[_DummyRule("dummy_response_rule", "response")]
        )
        req = _MockRequest(content=[{"type": "text", "text": "I don't have that tool"}])
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_response.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_response.append_session_assertion_event",
                   new_callable=AsyncMock):
            await transformer.transform(req, ctx)

        assert len(ctx.grounding_issues) == 1
        assert "dummy_response_rule" in ctx.grounding_issues[0]
        assert "use ToolSearch" in ctx.grounding_issues[0]
        assert not hasattr(ctx, "system")

    @pytest.mark.asyncio
    async def test_finding_recorded_in_ctx_state_assertion_findings(self):
        transformer = StateAssertionResponseTransformer(
            rules=[_DummyRule("dummy_response_rule", "response")]
        )
        req = _MockRequest(content=[{"type": "text", "text": "text"}])
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_response.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_response.append_session_assertion_event",
                   new_callable=AsyncMock):
            await transformer.transform(req, ctx)

        assert len(ctx.state_assertion_findings) == 1
        assert ctx.state_assertion_findings[0]["rule_id"] == "dummy_response_rule"

    @pytest.mark.asyncio
    async def test_session_snapshot_passed_to_rules(self):
        """Rules receive prior session events via session_snapshot so they can
        self-check escalation (e.g. 'has this subject been flagged before?')
        without awaiting anything themselves."""
        captured = {}

        class _CapturingRule(_DummyRule):
            def evaluate(self, **kwargs):
                captured.update(kwargs)
                return []

        transformer = StateAssertionResponseTransformer(
            rules=[_CapturingRule("capturing_rule", "response")]
        )
        req = _MockRequest(content=[{"type": "text", "text": "text"}])
        ctx = _make_ctx()

        prior_events = [{"rule_id": "deferred_denial", "subject": "X", "verdict": "contradicted"}]
        cached_deferred = ["EnterPlanMode", "mcp__playwright__playwright_get"]
        with patch("llm.transformers.state_assertion_response.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=prior_events), \
             patch("llm.transformers.state_assertion_response.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=cached_deferred):
            await transformer.transform(req, ctx)

        assert captured["session_snapshot"] == {
            "assertion_events": prior_events,
            "deferred_tool_names": cached_deferred,
        }

    @pytest.mark.asyncio
    async def test_multiple_findings_each_recorded(self):
        transformer = StateAssertionResponseTransformer(rules=[_DummyRule(
            "multi_rule", "response",
            findings=[
                AssertionFinding(rule_id="multi_rule", verdict="contradicted",
                                  evidence_snippet="a", subject="A",
                                  correction_note="note-a", severity="nudge"),
                AssertionFinding(rule_id="multi_rule", verdict="contradicted",
                                  evidence_snippet="b", subject="B",
                                  correction_note="note-b", severity="nudge"),
            ],
        )])
        req = _MockRequest(content=[{"type": "text", "text": "text"}])
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_response.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_response.append_session_assertion_event",
                   new_callable=AsyncMock) as mock_append:
            await transformer.transform(req, ctx)
            import asyncio
            await asyncio.sleep(0)

        assert len(ctx.grounding_issues) == 2
        assert len(ctx.state_assertion_findings) == 2
        assert mock_append.call_count == 2


def _log_severity_rule(id_, phase, subject="mcp__tool__x"):
    return _DummyRule(id_, phase, findings=[
        AssertionFinding(
            rule_id=id_, verdict="contradicted", evidence_snippet="ev",
            subject=subject, correction_note="already handled elsewhere",
            severity="log",
        )
    ])


class TestSeverityGating:
    """ADR-0039 correction: severity="log" findings must be recorded for the
    audit trail but must NOT trigger a duplicate nudge — some other mechanism
    (e.g. GuardrailTransformer's own error-loop guard) already acted."""

    @pytest.mark.asyncio
    async def test_request_shell_skips_system_note_for_log_severity(self):
        transformer = StateAssertionRequestTransformer(
            rules=[_log_severity_rule("no_progress", "request")]
        )
        req = _MockRequest(system=None)
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_request.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_request.append_session_assertion_event",
                   new_callable=AsyncMock) as mock_append:
            await transformer.transform(req, ctx)
            import asyncio
            await asyncio.sleep(0)

        assert req.system is None, "log-severity findings must not inject a system note"
        assert len(ctx.state_assertion_findings) == 1, "but must still be recorded for the audit trail"
        mock_append.assert_called_once()  # and still persisted to session cache

    @pytest.mark.asyncio
    async def test_request_shell_still_nudges_non_log_severity(self):
        """Regression: only 'log' is special-cased — 'nudge'/'refine'/'block'
        still inject the system note as before."""
        transformer = StateAssertionRequestTransformer(
            rules=[_DummyRule("deferred_denial", "request")]  # default severity="nudge"
        )
        req = _MockRequest(system=None)
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_request.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_request.append_session_assertion_event",
                   new_callable=AsyncMock):
            await transformer.transform(req, ctx)

        assert req.system is not None
        assert "use ToolSearch" in req.system

    @pytest.mark.asyncio
    async def test_response_shell_skips_grounding_issues_for_log_severity(self):
        transformer = StateAssertionResponseTransformer(
            rules=[_log_severity_rule("no_progress", "response")]
        )
        req = _MockRequest(content=[{"type": "text", "text": "text"}])
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_response.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_response.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_response.append_session_assertion_event",
                   new_callable=AsyncMock) as mock_append:
            await transformer.transform(req, ctx)
            import asyncio
            await asyncio.sleep(0)

        assert ctx.grounding_issues == [], "log-severity findings must not append to grounding_issues"
        assert len(ctx.state_assertion_findings) == 1
        mock_append.assert_called_once()

    @pytest.mark.asyncio
    async def test_response_shell_still_appends_non_log_severity(self):
        transformer = StateAssertionResponseTransformer(
            rules=[_DummyRule("deferred_denial", "response")]  # default severity="nudge"
        )
        req = _MockRequest(content=[{"type": "text", "text": "text"}])
        ctx = _make_ctx()

        with patch("llm.transformers.state_assertion_response.get_session_assertion_events",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_response.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.state_assertion_response.append_session_assertion_event",
                   new_callable=AsyncMock):
            await transformer.transform(req, ctx)

        assert len(ctx.grounding_issues) == 1
        assert "use ToolSearch" in ctx.grounding_issues[0]


class TestPipelineRegistration:
    """Confirm both shells are actually wired into the real proxy pipelines
    (proxy/proxy.py), not just importable in isolation — and in the order the
    ADR-0036 design requires (after IntentClassifier / after GroundingValidator).
    Also confirms the real pipeline builders construct them with the actual
    ACTIVE_RULE_CLASSES rules (proxy.py's module-level _STATE_ASSERTION_RULES),
    not an empty list."""

    def test_registered_in_request_pipeline_after_intent_classifier(self):
        from config import load_config
        from proxy.proxy import build_request_pipeline

        cfg = load_config()
        pipeline = build_request_pipeline(cfg, models_differ=False)
        names = pipeline.transformer_names

        assert "state_assertion_request" in names
        assert names.index("state_assertion_request") > names.index("intent_classifier")

    def test_registered_in_response_pipeline_after_grounding_validator(self):
        from config import load_config
        from proxy.proxy import build_response_pipeline

        cfg = load_config()
        pipeline = build_response_pipeline(cfg)
        names = pipeline.transformer_names

        assert "state_assertion_response" in names
        assert names.index("state_assertion_response") > names.index("grounding_validator")
        assert names.index("state_assertion_response") < names.index("model_feedback")

    def test_real_pipeline_transformers_carry_the_active_rules(self):
        from config import load_config
        from proxy.proxy import build_request_pipeline, _STATE_ASSERTION_RULES

        assert len(_STATE_ASSERTION_RULES) == 3
        assert {r.id for r in _STATE_ASSERTION_RULES} == {
            "deferred_denial", "no_progress", "exploration_grounding",
        }

        cfg = load_config()
        pipeline = build_request_pipeline(cfg, models_differ=False)
        request_transformer = next(
            t for t in pipeline._transformers if t.name == "state_assertion_request"
        )
        assert request_transformer._rules is _STATE_ASSERTION_RULES
