# llm/state_assertion.py
"""State-Assertion Verification Framework (ADR-0036).

Unifies the shared structural pattern behind ADR-0009 (loop-guard),
ADR-0031/0033 (completion/generality-claim grounding), and ADR-0037 (this
repo's plan-mode false-positive incident): a model asserts something about its
own state or capabilities; the proxy can compute the real signal
deterministically from messages/session-cache/request.tools; if the assertion
contradicts the real signal, the proxy corrects — before the model acts on it
(request-phase rules) or after it already has (response-phase rules).

This module is the pure core: no I/O, no pipeline dependency beyond the model-
fragility predicate. Two thin transformers
(llm/transformers/state_assertion_request.py and
state_assertion_response.py) call evaluate_rules() from within the real
request/response pipelines (proxy/proxy.py).

There is no module-level rule registry here (there was, briefly — see
llm/state_assertion_rules/__init__.py's docstring for why it was removed).
Callers pass the list of active rules explicitly: proxy/proxy.py builds it
once via llm.state_assertion_rules.build_active_rules() and hands it to each
transformer's constructor; tests build their own local lists. evaluate_rules()
is a pure function of its inputs — no hidden global read.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from utils.tool_utils import is_fragile_orchestration_model

logger = logging.getLogger(__name__)

Phase = Literal["request", "response"]
Severity = Literal["block", "refine", "nudge", "log"]


@dataclass(frozen=True)
class AssertionFinding:
    """A single detected contradiction between a model assertion and the real,
    proxy-computable signal."""
    rule_id: str
    verdict: str            # "contradicted" | "unverifiable"
    evidence_snippet: str    # trigger excerpt from output/history (<=200 chars)
    subject: str             # the tool/file/state in question
    correction_note: str     # deterministic nudge ready to inject
    severity: Severity


class StateAssertionRule(Protocol):
    """A single rule in the registry. See ADR-0038/0039/0040 for concrete rules."""

    id: str
    phase: Phase
    agnostic: bool  # True = runs for every model; False = only fragile models

    def evaluate(
        self,
        *,
        content: Any,
        messages: list,
        ctx: Any,
        tools: list | None,
        model: str,
        session_snapshot: dict,
    ) -> list[AssertionFinding]:
        """Must be synchronous — the shells (state_assertion_request.py,
        state_assertion_response.py) already awaited session-cache lookups
        before calling evaluate_rules(). `session_snapshot` carries whatever a
        rule needs, pre-fetched by the calling shell. Keys currently provided:
        `"assertion_events"` (raw entries from get_session_assertion_events —
        each has rule_id/subject/verdict/timestamp, for a rule to count prior
        occurrences of its own rule_id+subject) and, on the response shell,
        `"deferred_tool_names"` (this session's cached
        <available-deferred-tools> list, for deferred_denial — ADR-0038). A
        rule should use `.get(key, default)` rather than assume every key is
        present, since request-phase and response-phase shells populate
        different keys."""
        ...


def evaluate_rules(
    phase: Phase,
    rules: list[StateAssertionRule],
    *,
    content: Any,
    messages: list,
    ctx: Any,
    tools: list | None,
    model: str,
    session_snapshot: dict,
) -> list[AssertionFinding]:
    """Run every rule in `rules` matching `phase`, gated by `agnostic`/model fragility.

    `rules` is explicit, not a module global — the caller (a transformer's
    `self._rules`, or a test's local list) owns the list; this function never
    reads or mutates any shared state of its own.

    A rule with agnostic=False only runs for models matched by
    is_fragile_orchestration_model (ADR-0037) — expensive/perturbing
    interventions stay opt-in per model; cheap detection+nudge rules
    (agnostic=True) run for every model, matching quality_refinement.py's
    existing agnostic-by-design doctrine.

    Each rule's evaluate() is isolated in its own try/except. Pipeline.process()
    (llm/pipeline.py) logs and RE-RAISES any exception a transformer lets
    escape, and the non-streaming response path (server.py) has no fallback
    for that — an uncaught exception here would turn a real, already-generated
    model response into a lost turn and an HTTP error for the user, not just a
    missing nudge. A bug in one rule (regex on unexpected input shape, a
    missing dict key, etc.) must never do that to every other rule or to the
    request itself — so a rule that raises is skipped for this turn, logged,
    and every other rule still runs.
    """
    findings: list[AssertionFinding] = []
    is_fragile = is_fragile_orchestration_model(model)
    for rule in rules:
        if rule.phase != phase:
            continue
        if not rule.agnostic and not is_fragile:
            continue
        try:
            findings.extend(rule.evaluate(
                content=content, messages=messages, ctx=ctx, tools=tools,
                model=model, session_snapshot=session_snapshot,
            ))
        except Exception as exc:
            logger.warning(
                "[state-assertion] rule '%s' raised %s: %s — skipped for this turn, "
                "other rules unaffected",
                getattr(rule, "id", rule.__class__.__name__), type(exc).__name__, exc,
            )
    return findings
