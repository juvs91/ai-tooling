# ADR-0038: Deferred-Tool Denial Rule

**Status:** Accepted
**Date:** 2026-08-03
**Supersedes:** —
**Superseded by:** —

---

## Context

F-001 (`docs/findings/FINDINGS.md`) direction 1 — the direction with the most
direct evidence in the source incident (`bad_conversation.txt`, 7847 lines,
triaged in
`ai-notes/analysis/kimi-school-system-drift-triage-2026-08-02.md`): Kimi K2
searches for a tool (`mcp__playwright__playwright_get`), doesn't find it in
its visible tool list, concludes "not connected" instead of recognizing it as
a **deferred tool** (requires `ToolSearch` first), and calls unrelated
BigQuery tools by mistake instead — repeatedly, across ~2800 lines. ~4000
lines later, needing `EnterPlanMode` (also deferred), it reasons "There's an
EnterPlanMode tool? Not listed... I don't have EnterPlanMode tool" — despite
having read, via a successful `WebFetch` earlier in the *same* conversation, a
complete tutorial explaining exactly what a deferred tool is and the three
`ToolSearch` query forms. Confirmed by grep: the model never called
`ToolSearch` once in the full 7847-line transcript.

ADR-0037 already fixed the specific plan-mode instance of this pattern (never
letting `EnterPlanMode`/`ExitPlanMode` become unreachable for fragile models).
This ADR generalizes the fix to any tool the proxy already knows about —
Playwright, other MCP servers, or CC's own deferred workflow tools — by
registering the framework's first rule (ADR-0036): `deferred_denial`.

## Decision

### Detection — sentence-level co-occurrence, mirrors ADR-0031/0033

`llm/state_assertion_rules/deferred_denial.py`, `DeferredDenialRule`:

- `phase = "response"`, `agnostic = True` — a correct-by-construction nudge
  (only fires when the tool provably exists) is cheap and helpful for any
  model, not just fragile ones; consistent with `quality_refinement.py`'s
  deliberately agnostic design.
- A denial phrase regex (EN/ES: "I don't have", "isn't listed/available/
  connected", "no tengo esa tool", "no está conectada", etc.) must co-occur in
  the **same sentence** (reusing the `_SENTENCE_SPLIT_RE` pattern from
  `grounding_validator.py`) as a tool-name token — exactly the verb+object
  co-occurrence design `_extract_completion_claims`/`_extract_generality_claims`
  already use, to avoid false positives like "WebFetch does exist, I already
  used it."
- **Only fires when the named tool is one the proxy provably knows about** —
  never a generic capitalized-word heuristic. Two sources of truth, both
  already existing (no new catalog invented):
  1. `_CC_WORKFLOW_TOOL_NAMES` (static, `utils/tool_utils.py`) plus this
     session's cached `<available-deferred-tools>` list
     (`get_session_deferred_tools` — same cache `DeferredToolsTransformer`
     already populates), for exact-name matches.
  2. `_MCP_TOOL_RE` (`utils/tool_utils.py`) for `mcp__server__tool` shaped
     tokens — the same regex the proxy itself uses to recognize legitimate
     MCP tools in `validate_tool_name_with_deferred_bypass`.

### Correction

`correction_note`: `"'{tool}' is a deferred tool — it DOES exist. Call
ToolSearch('select:{tool}') before concluding it isn't available."` —
deterministic, names the exact remedial action, matches F-001 direction 1's
proposed wording.

### Session snapshot extension (implementation correction over the ADR-0036 sketch)

`StateAssertionResponseTransformer` (ADR-0036) originally only pre-fetched
`assertion_events` for rules. This rule also needs "which MCP tools has this
session's `<available-deferred-tools>` cache actually recorded?" — a
different cache (`deferred_tools_cache.py`) than the state-assertion event
log. The shell now additionally awaits `get_session_deferred_tools(session_id)`
and adds it to `session_snapshot["deferred_tool_names"]`. Both keys are
optional per the rule protocol's contract (`session_snapshot.get(key,
default)`) since request-phase and response-phase shells populate different
keys.

### Activation

`llm/state_assertion_rules/__init__.py` imports `deferred_denial`, which
appends `DeferredDenialRule()` to `RULES_REGISTRY` on import (self-registering
module, standard plugin pattern). `proxy/proxy.py` imports the
`state_assertion_rules` package once — the single canonical activation site.
Tests import individual rule modules directly and use an autouse fixture to
snapshot/clear/restore `RULES_REGISTRY` around each test, so global
registration state never leaks between tests regardless of import order.

## Explicitly Out of Scope

- Cross-turn escalation ("this tool has been denied 3+ times — do something
  stronger than a nudge") — `session_snapshot["assertion_events"]` (ADR-0036)
  already carries this session's prior findings, so a future revision could
  filter it for repeated `rule_id="deferred_denial"` occurrences without any
  new plumbing; this rule doesn't do that yet, severity stays `"nudge"`
  unconditionally. A future ADR can add escalation without touching this
  rule's detection logic.
- Detecting denials of tools the proxy does NOT know about (a model correctly
  saying a nonexistent tool doesn't exist) — by design, this rule only
  contradicts provably-false denials, never flags true ones.
- Non-Claude models' native (non-XML) tool-call mechanisms bypassing text
  entirely — this rule only inspects response TEXT content blocks; a model
  that emits a malformed `tool_use` instead of denying in prose is a
  different failure mode (`structural_tool_validator.py`, ADR-0016), not this
  rule's territory.

## Consequences

- The exact incident scenario (denying `mcp__playwright__playwright_get`,
  denying `EnterPlanMode`) now gets an immediate, same-turn corrective nudge
  fed into the existing quality-refinement re-request loop via
  `ctx.grounding_issues` — the model gets a second chance to call
  `ToolSearch` instead of persisting the wrong belief.
- Zero new false-positive surface for models never denying a real tool —
  the rule requires exact-name/regex-confirmed existence before firing.
- Residual, accepted limitation: only catches denials expressed as prose the
  model outputs. A model that silently gives up (no denial text, just stops
  trying) or reroutes to an unrelated tool without narrating why is not
  caught by this rule — that gap is closer to ADR-0039's `no_progress` rule
  (next in the roadmap).

## Files Changed

- `vendor/claude-code-proxy/llm/state_assertion_rules/__init__.py` — new,
  registers all rules (this ADR's `deferred_denial`, future rules append
  here).
- `vendor/claude-code-proxy/llm/state_assertion_rules/deferred_denial.py` —
  new, the rule.
- `vendor/claude-code-proxy/llm/transformers/state_assertion_response.py` —
  extended to also pre-fetch `get_session_deferred_tools` into
  `session_snapshot`.
- `vendor/claude-code-proxy/llm/state_assertion.py` — docstring updated to
  document the `session_snapshot` keys shells actually populate.
- `vendor/claude-code-proxy/proxy/proxy.py` — imports
  `llm.state_assertion_rules` once (activation site).
- `vendor/claude-code-proxy/tests/test_deferred_denial_rule.py` — new, rule
  unit tests (detection, catalog matching, no-false-positive cases,
  multi-tool sentences, mcp__ pattern matching).
- `vendor/claude-code-proxy/tests/test_state_assertion_transformers.py` —
  updated `session_snapshot` shape assertion for the new
  `deferred_tool_names` key.

## Verification

- Full proxy suite: 1259 passed (up from 1243 after ADR-0036; +16 new tests,
  0 regressions).
- Rule-level tests confirm: denial of a known static-catalog tool
  (`EnterPlanMode`) fires; denial of a known session-cached MCP tool
  (`mcp__playwright__playwright_get`) fires; denial of an UNKNOWN name does
  NOT fire (no catalog match); a sentence merely mentioning a tool without a
  denial phrase does not fire; ES denial phrases fire equivalently to EN;
  multiple distinct tool denials in one response each produce their own
  finding, deduplicated per subject.
- Reconstructed fixture from `bad_conversation.txt` lines 7822-7846 ("I don't
  have EnterPlanMode tool") run through `DeferredDenialRule.evaluate()` ⇒ one
  finding, `subject="EnterPlanMode"`, `correction_note` citing
  `ToolSearch('select:EnterPlanMode')`.
