# ADR-0039: No-Progress Rule (Failure-Driven, Reusing Existing Guardrails)

**Status:** Accepted
**Date:** 2026-08-03
**Supersedes:** —
**Superseded by:** —

---

## Context

F-001 (`docs/findings/FINDINGS.md`) direction 2: "generalize the no-progress
loop heuristic (ADR-0009, today limited to `Read`) to any tool family — if the
same tool/family fails or is retried 3+ times without a change of strategy,
force a refinement nudge before continuing." The plan for this ADR (see the
approved implementation plan) further refined the signal: the primary trigger
should be **real tool failure** (`tool_result.is_error`/error content), not
mere call repetition — repeating a call with the same args isn't inherently
suspicious (legitimate pagination, polling), but the same tool failing
repeatedly without a strategy change is the actual incident signature.

Before writing this rule, its detection logic was checked against what
already exists in this proxy (per `AGENTS.md`'s deduplication mandate: "before
writing any new tool… consult existing capabilities"). That check surfaced
that **the generalization already exists**:

- `llm/transformers/guardrail.py`'s `_detect_consistently_failing_tools`
  (threshold=2, window=20 messages) scans `tool_use`→`tool_result` pairs
  across conversation history for **any** tool name — no hardcoded list — and
  detects errors via `is_error=True` (Anthropic passthrough), a `[ERROR]`
  prefix (LiteLLM path, added by `converters.py`), or `None` content
  (silent/communication failure). `GuardrailTransformer` already **hard-blocks**
  the failing tool for that turn (removes it from `request.tools`) and
  injects an explanatory system note — a stronger intervention than a nudge.
- `_detect_stuck_tool_calls` covers the sibling case: identical inputs
  repeated ≥2 times with no error but no new information either (e.g. "Directory
  listing is not enabled") — also tool-family-agnostic, also hard-blocking.
- `_detect_duplicate_reads` remains the one Read/MCP-resource-tool-specific
  check (ADR-0009's original scope) — legitimately narrower, since re-reading
  the same file is a distinct pattern from generic tool failure.

`utils/quality.py`'s H16 — the check F-001/the triage doc actually meant by
"today limited to Read" — is a **different, narrower, turn-scoped** heuristic
inside the quality-scoring loop (`quality_refinement.py`), unrelated to
`guardrail.py`'s history-scanning mechanism. The generalization F-001 asked
for was already shipped, just not where the triage doc looked for it.

**Also relevant to this rule's design**: in the source incident
(`bad_conversation.txt`), the model never actually *called*
`mcp__playwright__playwright_get` with a resulting error — it reasoned in
text that the tool "wasn't connected" and called BigQuery instead (a
different, technically-succeeding tool). `_detect_consistently_failing_tools`
would not catch that scenario (no `tool_result` error exists to detect) —
that failure mode is exactly what ADR-0038's `deferred_denial` rule already
addresses by inspecting the model's own denial text, not tool-call outcomes.
This confirms the two rules are complementary, not overlapping: `deferred_denial`
catches "avoided calling a tool that exists"; `no_progress` catches "kept
calling the same tool and it kept failing."

## Decision

**Do not reimplement failure-loop detection.** `NoProgressRule`
(`llm/state_assertion_rules/no_progress.py`) directly reuses
`_detect_consistently_failing_tools`/`_detect_stuck_tool_calls` from
`llm/transformers/guardrail.py` and converts their output into
`AssertionFinding`s with `severity="log"`.

`severity="log"` is the operative decision: it tells both shells
(`state_assertion_request.py`) to **record the finding for the audit trail
and session-cache history (ADR-0036's infrastructure) without re-injecting a
system note or otherwise re-acting** — `GuardrailTransformer` already injected
its own note and already blocked the tool in the same request-pipeline pass
(`proxy/proxy.py`'s `build_request_pipeline` runs `GuardrailTransformer`
after `StateAssertionRequestTransformer`, but the ordering doesn't matter for
this rule since it reads only `messages`, i.e. prior turns — never the
current turn's not-yet-generated content). This closes the "does the loop
signal get session-level escalation like ADR-0031/0033?" gap without
duplicating or double-nudging.

Both shells (`state_assertion_request.py`, `state_assertion_response.py`)
were updated to skip their nudge-injection step when `finding.severity ==
"log"` — this was a necessary correction to ADR-0036's original shells, which
unconditionally nudged every finding regardless of severity.

`phase = "request"`, `agnostic = True` (visibility into these already-agnostic
guardrail decisions is useful for every model, not just fragile ones).

## Explicitly Out of Scope

- Changing `GuardrailTransformer`'s thresholds, window, or blocking behavior
  — untouched, still the sole enforcement mechanism.
- Catching the "avoided a tool without ever calling it" failure mode — that's
  `deferred_denial` (ADR-0038)'s territory, not this rule's.
- Cross-turn escalation beyond what `GuardrailTransformer` already does per
  turn (it re-evaluates the same window every request, so a tool failing
  across many turns keeps getting blocked/noted turn after turn already) —
  `session_snapshot["assertion_events"]` (ADR-0036) is available if a future
  ADR wants a stronger session-level response (e.g. escalating to `"refine"`
  after N session-wide occurrences), deliberately not added here to keep this
  ADR's change surface minimal.
- Consolidating `utils/quality.py`'s H16 (Read/Glob/Grep-specific, turn-scoped)
  into this rule or removing it — H16 lives in a different pipeline phase
  (response, quality scoring) with a different purpose (score penalty, not a
  blocking guard); left untouched as a separate, narrower signal.

## Consequences

- No new failure-detection logic — zero new false-positive/false-negative
  surface beyond what `GuardrailTransformer` already has in production,
  already tested (`tests/test_guardrail.py` if present, or wherever its
  existing suite lives).
- The state-assertion audit trail (`ctx.state_assertion_findings`, session
  cache) now includes every error-loop/stuck-loop block `GuardrailTransformer`
  performs, giving the same observability ADR-0031/0033/0038's findings
  already have, for free.
- Corrected a real ADR-0036 gap: both shells now respect `severity`, so future
  audit-only rules don't produce duplicate/redundant nudges.
- Residual, accepted limitation: this rule inherits every limitation
  `_detect_consistently_failing_tools`/`_detect_stuck_tool_calls` already have
  (window=20 messages, threshold=2) — not re-evaluated or re-tuned here.

## Files Changed

- `vendor/claude-code-proxy/llm/state_assertion_rules/no_progress.py` — new,
  the rule (reuses `guardrail.py` detections, does not reimplement them).
- `vendor/claude-code-proxy/llm/state_assertion_rules/__init__.py` — registers
  `no_progress`.
- `vendor/claude-code-proxy/llm/transformers/state_assertion_request.py` —
  gate `ensure_system_note` on `finding.severity != "log"`.
- `vendor/claude-code-proxy/llm/transformers/state_assertion_response.py` —
  gate `ctx.grounding_issues` append on `finding.severity != "log"`, for the
  same reason, symmetrically, even though no response-phase rule uses `"log"`
  yet.
- `vendor/claude-code-proxy/tests/test_no_progress_rule.py` — new, rule unit
  tests (reuses `guardrail.py`'s own fixture-building style).
- `vendor/claude-code-proxy/tests/test_state_assertion_transformers.py` —
  added severity-gating regression tests for both shells.

## Verification

- Full proxy suite: 1272 passed (up from 1259 after ADR-0038; +13 new tests,
  0 regressions).
- Rule tests confirm: a tool with ≥2 recent errors produces exactly one
  `log`-severity finding per tool, `subject` = tool name, `evidence_snippet`
  reflects the real error count; a tool called with identical input ≥2 times
  with no new information produces a finding via the stuck-loop path; a tool
  with < threshold occurrences produces no finding; the rule never crashes on
  empty message history.
- Shell regression tests confirm: a `"log"`-severity finding does NOT call
  `ensure_system_note`/append to `ctx.grounding_issues`, but IS still recorded
  in `ctx.state_assertion_findings` and persisted via
  `append_session_assertion_event` — the split between "audit" and "act" is
  exact.
