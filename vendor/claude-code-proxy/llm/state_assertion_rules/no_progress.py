# llm/state_assertion_rules/no_progress.py
"""no_progress rule (ADR-0039).

F-001 direction 2 asked to "generalize the no-progress loop heuristic (ADR-0009,
today limited to Read/Glob/Grep) to any tool family." Investigating before
writing this rule surfaced that the generalization already exists:
`llm/transformers/guardrail.py`'s `_detect_consistently_failing_tools` and
`_detect_stuck_tool_calls` are already tool-family-agnostic (no hardcoded tool
list — they work off tool_use/tool_result pairs regardless of name), already
scan conversation history (not just the current turn, unlike
utils/quality.py's H16, which IS the Read/Glob/Grep-specific, turn-scoped
check ADR-0009/F-001 were actually describing), and already hard-block the
offending tool by removing it from `request.tools` for the turn — a stronger
intervention than this framework's nudge-only shells provide.

Reimplementing that detection here (a second, parallel failure-tracking
mechanism) would violate AGENTS.md's deduplication mandate and risk producing
two independent, possibly-inconsistent judgments about the same tool_result
history. This rule does NOT reimplement detection — it REUSES
`_detect_consistently_failing_tools`/`_detect_stuck_tool_calls` directly and
surfaces their findings into the state-assertion framework's audit trail
(ctx.state_assertion_findings, session-cache persistence via
append_session_assertion_event — ADR-0036) purely for observability and
consistency with the rest of the framework. Severity is "log": the shells
(state_assertion_request.py) skip re-injecting a system note for "log"
findings, because GuardrailTransformer already injected its own note and
already blocked the tool in the SAME request pipeline pass — see
proxy/proxy.py's build_request_pipeline for the actual ordering.
"""
from __future__ import annotations

from llm.state_assertion import AssertionFinding
from llm.transformers.guardrail import (
    _detect_consistently_failing_tools,
    _detect_stuck_tool_calls,
)


class NoProgressRule:
    """Mirrors GuardrailTransformer's existing error-loop/stuck-loop guards
    into the state-assertion audit trail. Does not duplicate their blocking."""

    id = "no_progress"
    phase = "request"
    agnostic = True  # audit-only visibility is useful for every model

    def evaluate(self, *, content, messages, ctx, tools, model, session_snapshot):
        findings: list[AssertionFinding] = []

        for tool_name, count in _detect_consistently_failing_tools(messages).items():
            findings.append(AssertionFinding(
                rule_id=self.id,
                verdict="contradicted",
                evidence_snippet=f"'{tool_name}' returned errors {count} time(s) in recent history",
                subject=tool_name,
                correction_note=(
                    f"'{tool_name}' failed {count} time(s) recently and was blocked "
                    "this turn by the proxy's error-loop guard."
                ),
                severity="log",
            ))

        for tool_name, count in _detect_stuck_tool_calls(messages).items():
            findings.append(AssertionFinding(
                rule_id=self.id,
                verdict="contradicted",
                evidence_snippet=(
                    f"'{tool_name}' called {count} time(s) with identical input, "
                    "no new information"
                ),
                subject=tool_name,
                correction_note=(
                    f"'{tool_name}' was called {count} time(s) with identical input "
                    "and was blocked this turn by the proxy's stuck-loop guard."
                ),
                severity="log",
            ))

        return findings


# No import-time registration here — see llm/state_assertion_rules/__init__.py's
# ACTIVE_RULE_CLASSES + build_active_rules() for the single, explicit construction site.
