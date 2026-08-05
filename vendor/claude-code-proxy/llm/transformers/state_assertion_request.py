# llm/transformers/state_assertion_request.py
"""State-Assertion Request Transformer (ADR-0036).

Thin pipeline adapter: runs request-phase state-assertion rules and injects a
preventive system note for any contradiction found, before the model acts on
it. Rules are injected explicitly via the constructor (`rules` — see
llm/state_assertion_rules/build_active_rules(), called once by
proxy/proxy.py) rather than read from a module-level global — this
transformer owns no shared state of its own.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from llm.compressor import append_session_assertion_event, get_session_assertion_events
from llm.pipeline import Transformer, TransformContext
from llm.state_assertion import evaluate_rules, StateAssertionRule
from utils.utils import ensure_system_note

logger = logging.getLogger(__name__)


class StateAssertionRequestTransformer(Transformer):
    """Runs phase="request" state-assertion rules and injects preventive nudges."""

    def __init__(self, rules: list[StateAssertionRule]) -> None:
        self._rules = rules

    @property
    def name(self) -> str:
        return "state_assertion_request"

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        messages = getattr(request, "messages", None) or []
        model = str(getattr(request, "model", "") or "")

        # See state_assertion_response.py: rules run synchronously, so any
        # prior-session state needed for escalation is fetched here.
        assertion_events: list[dict] = []
        if ctx.session_id:
            try:
                assertion_events = await get_session_assertion_events(ctx.session_id)
            except Exception as exc:
                logger.warning("[state-assertion] Failed to load session snapshot: %s", exc)

        findings = evaluate_rules(
            "request",
            self._rules,
            content=None,
            messages=messages,
            ctx=ctx,
            tools=getattr(request, "tools", None),
            model=model,
            session_snapshot={"assertion_events": assertion_events},
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
            # already acted on this (e.g. GuardrailTransformer's own
            # error-loop/stuck-loop guards already injected their own note and
            # blocked the tool — ADR-0039's no_progress rule mirrors those
            # into this audit trail without re-nudging the same thing twice).
            if finding.severity != "log":
                # Correct call target: `request` (has a real .system field the
                # model sees), NOT `ctx` — see state_assertion_response.py's
                # docstring for why ensure_system_note(ctx, ...) is a no-op.
                ensure_system_note(request, finding.correction_note)
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
            "[state-assertion] request-phase: %d finding(s): %s",
            len(findings), [f.rule_id for f in findings],
        )
