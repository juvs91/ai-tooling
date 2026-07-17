# ADR-0023 — Plan mode: tool descriptions + Spanish stem regex + EnterPlanMode enforcement

**Status:** Accepted  
**Date:** 2026-07-16  
**Author:** juvs

---

## Context

Three bugs discovered via Kimi K2 chain-of-thought analysis (session 2026-07-16):

### Bug 1 — PLANNING_RE: Spanish stems don't match conjugated forms

`PLANNING_RE` in `router/llm_router.py` uses stem patterns like `dise[ñn]`, `estrateg`,
`evalua`, `planific`, `compar` to detect Spanish planning intent. The pattern ends with `)\b`.

In Python 3, ALL Unicode letters (including ñ, í, é) are treated as `\w` (verified:
`re.match(r'\w', 'ñ')` returns a match). This means `\b` after `dise[ñn]` in "diseña"
FAILS because ñ is `\w` and "a" is also `\w` — there is no word boundary between them.

**Consequence:** "diseña e implementa" → `PLANNING_RE` returns `plan=False` → regex fallback
classifies as BUILD, not PLAN. The LLM classifier handles it correctly, but if it times out
or the circuit breaker is open, the proxy incorrectly routes a planning prompt as BUILD,
stripping `EnterPlanMode`/`ExitPlanMode` from Kimi's tool list.

**Fix:** Add `\w*` to all Spanish stem patterns so they match the full conjugated form.
`dise[ñn]\w*` matches "diseña", "diseñar", "diseñe", "diseñado", etc.

### Bug 2 — Missing descriptions for EnterPlanMode and ExitPlanMode

`_CC_TOOL_DESCRIPTIONS` in `deferred_tools.py` has entries for AskUserQuestion, TodoWrite,
WebSearch, WebFetch, and EnterWorktree — but NOT for EnterPlanMode or ExitPlanMode. These
fall back to: `"Claude Code built-in workflow tool: {name}. Use the input schema."` — completely
uninformative. Kimi K2 cannot determine when to call them or how they differ from similar tools
(e.g., ExitWorktree). This caused Kimi to call ExitWorktree by mistake in the 2026-07-16 test.

**Fix:** Add explicit descriptions that state: what the tool does, WHEN to call it (EnterPlanMode
first, ExitPlanMode last), and explicitly warn it is NOT ExitWorktree.

### Bug 3 — _PLAN_MODE_EXIT_NOTE doesn't require EnterPlanMode first

When CC plan mode is already active (Signal 1: "Plan mode is active" in system prompt),
`PlanModeEnforcementTransformer` Case 2 fires `_PLAN_MODE_EXIT_NOTE`:

> "Estás en plan mode. Cuando el plan esté completo, llama ExitPlanMode."

This note does NOT tell Kimi to call `EnterPlanMode` first. Consequently, Kimi skips
`EnterPlanMode` and the proxy records `_pm_source = "cc"`. When CC later exits plan mode
(user approves the plan), P0_UNLOCK fires (`_pm_source == "cc"` + intent=BUILD), setting
`plan_mode_active=False` and stripping `ExitPlanMode` from the tool list for subsequent turns.

**Fix:** Update `_PLAN_MODE_EXIT_NOTE` to require EnterPlanMode as the first tool call
if the plan session hasn't been formally opened by the model yet.

---

## Decision

Apply all three fixes in a single change across three files:

1. `router/llm_router.py` — Add `\w*` to Spanish stem patterns in `PLANNING_RE`
2. `llm/transformers/deferred_tools.py` — Add descriptions for EnterPlanMode/ExitPlanMode
3. `llm/transformers/plan_mode_enforcement.py` — Update `_PLAN_MODE_EXIT_NOTE` to require EnterPlanMode first

---

## Consequences

- Regex fallback correctly classifies "diseña e implementa" as PLAN even without LLM classifier
- Kimi K2 knows what EnterPlanMode/ExitPlanMode do and which to call first vs. last
- `_pm_source` changes from "cc" to "proxy" when Kimi calls EnterPlanMode → P0_UNLOCK
  no longer fires → ExitPlanMode remains available through the full plan session
- No impact on Claude native model (it uses CC's own plan mode signaling, not the proxy's)
- No impact on BUILD/VERIFY sessions (PLAN_ONLY_TOOLS are still gated by plan_mode_active)
