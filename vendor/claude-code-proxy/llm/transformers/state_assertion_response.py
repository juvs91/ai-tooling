# llm/transformers/state_assertion_response.py
"""State-Assertion Response Transformer (ADR-0036).

Thin pipeline adapter: runs response-phase state-assertion rules against the
model's own response text/tool calls, after it has already acted. Rules are
injected explicitly via the constructor (`rules` — see
llm/state_assertion_rules/build_active_rules(), called once by
proxy/proxy.py) rather than read from a module-level global — this
transformer owns no shared state of its own.

Findings are NOT injected via ensure_system_note(ctx, ...) here. TransformContext
(llm/pipeline.py) has no `system` attribute, so that call pattern — already
present in grounding_validator.py for ADR-0031/0033 — is a documented no-op:
it just sets an attribute on ctx that nothing reads. The real, working channel
for response-phase correction is ctx.grounding_issues, which
quality_refinement.py's _build_grounding_feedback() already consumes to build
re-request feedback text — the same mechanism ADR-0031/0033 rely on in
practice. This transformer reuses that channel rather than repeating the
no-op call pattern.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from llm.compressor import (
    append_session_assertion_event,
    get_session_assertion_events,
    get_session_deferred_tools,
)
from llm.pipeline import Transformer, TransformContext
from llm.state_assertion import evaluate_rules, StateAssertionRule

logger = logging.getLogger(__name__)


class StateAssertionResponseTransformer(Transformer):
    """Runs phase="response" state-assertion rules against the model's own output."""

    def __init__(self, rules: list[StateAssertionRule]) -> None:
        self._rules = rules

    @property
    def name(self) -> str:
        return "state_assertion_response"

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        messages = getattr(request, "messages", None) or []
        model = str(getattr(request, "model", "") or "")
        content = getattr(request, "content", None)

        # Rules run synchronously (evaluate() cannot await), so any prior-session
        # state a rule needs (escalation history, or — for deferred_denial,
        # ADR-0038 — which MCP tools this session's <available-deferred-tools>
        # cache actually contains) must be fetched here and handed in as a snapshot.
        assertion_events: list[dict] = []
        deferred_tool_names: list[str] = []
        if ctx.session_id:
            try:
                assertion_events = await get_session_assertion_events(ctx.session_id)
            except Exception as exc:
                logger.warning("[state-assertion] Failed to load session snapshot: %s", exc)
            try:
                deferred_tool_names = await get_session_deferred_tools(ctx.session_id)
            except Exception as exc:
                logger.warning("[state-assertion] Failed to load deferred-tools snapshot: %s", exc)

        findings = evaluate_rules(
            "response",
            self._rules,
            content=content,
            messages=messages,
            ctx=ctx,
            tools=getattr(ctx, "tools", None),
            model=model,
            session_snapshot={
                "assertion_events": assertion_events,
                "deferred_tool_names": deferred_tool_names,
            },
        )
        if not findings:
            return

        for finding in findings:
            ctx.state_assertion_findings.append({
                "rule_id": finding.rule_id,
                "subject": finding.subject,
                "verdict": finding.verdict,
                "severity": finding.severity,
                "correction_note": finding.correction_note,
                "evidence_snippet": finding.evidence_snippet,
            })
            # severity="log" means some other, already-existing mechanism
            # already corrected this (see llm/state_assertion_rules/no_progress.py,
            # ADR-0039) — record for the audit trail only, don't double-nudge.
            if finding.severity != "log":
                ctx.grounding_issues.append(
                    f"[state-assertion:{finding.rule_id}] {finding.correction_note}"
                )
            if ctx.session_id:
                try:
                    asyncio.create_task(
                        append_session_assertion_event(
                            ctx.session_id, finding.rule_id, finding.subject,
                            finding.verdict, finding.correction_note,
                        )
                    )
                except Exception as exc:
                    logger.warning("[state-assertion] Failed to schedule event persistence: %s", exc)

        logger.info(
            "[state-assertion] response-phase: %d finding(s): %s",
            len(findings), [f.rule_id for f in findings],
        )
