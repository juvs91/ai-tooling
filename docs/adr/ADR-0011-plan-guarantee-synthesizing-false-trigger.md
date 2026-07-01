# ADR-0011: Eliminate `_phase_is_plan` from plan_guarantee in DeferredTools

**Status:** Accepted  
**Date:** 2026-06-30  
**Refs:** ADR-0010 (plan_mode_source tracking), ADR-0008 (Signal 4 implicit exit)

---

## Context

Post-deploy of ADR-0010 fixes (Fixes 1–5), a new plan-mode re-trigger was observed in
a school-system TDD session. The session was in BUILD/EXECUTE phase, plan mode had been
correctly exited via ExitPlanMode (`plan_mode_active=False`), and Kimi was implementing
tests. After 6+ consecutive Read calls, Override D fired:

```
OVERRIDE D: READ → SYNTHESIZING (reads=6 >= 6)
intent=SYNTHESIZING phase=PLAN
[deferred-tools] injected 1 tool(s) via plan_guarantee (phase=PLAN): EnterPlanMode
[stuck-loop-guard] Blocked 'EnterPlanMode' after 2 identical call(s)
```

The `stuck-loop-guard` blocked the first 2 attempts but resets after 2; the 3rd call went
through → CC entered plan mode → P0_PLAN_LOCK trapped the session.

### Root Cause

`deferred_tools.py` Step 4 (plan_guarantee) had this condition:

```python
_phase_is_plan = ctx.phase == "PLAN"
if _phase_is_plan or ctx.plan_mode_active:
    # inject EnterPlanMode, ExitPlanMode, AskUserQuestion, TodoWrite
```

`SYNTHESIZING` maps to `phase=PLAN` in the classifier. When Override D fires during a BUILD
session, the SYNTHESIZING intent gets classified to phase=PLAN, making `_phase_is_plan=True`.
This injects `EnterPlanMode` into the tool list even though `ctx.plan_mode_active=False`.

The inline comment called `_phase_is_plan` a "belt-and-suspenders fallback for the very first
turn before the classifier has run." This rationale is incorrect: `IntentClassifierTransformer`
runs **before** `DeferredToolsTransformer` in the pipeline — `ctx.plan_mode_active` is always
set when DeferredTools executes. The fallback was never needed and introduced this false trigger.

---

## Decision

Remove `_phase_is_plan` from the plan_guarantee condition. The authoritative signal for plan
mode is `ctx.plan_mode_active`, set by IntentClassifier (Signals 0–3) before this transformer
runs.

**Change in `vendor/claude-code-proxy/llm/transformers/deferred_tools.py:320-321`:**

```python
# BEFORE:
_phase_is_plan = ctx.phase == "PLAN"
if _phase_is_plan or ctx.plan_mode_active:

# AFTER:
if ctx.plan_mode_active:
```

The `_phase_is_plan` variable is removed entirely (no longer referenced).

The log message is updated to reference `plan_mode_active` as the trigger, not phase.

---

## Consequences

**Positive:**
- `EnterPlanMode` and `ExitPlanMode` are only injected when plan mode is genuinely active.
- SYNTHESIZING phase (reads ≥ 6 during BUILD) can no longer trigger plan mode re-entry.
- Reduces tool list size during BUILD/EXECUTE sessions (fewer spurious deferred tool defs).

**Risks / Mitigations:**
- **Risk:** First turn of a plan session has no EnterPlanMode injected if `plan_mode_active=False`.  
  **Mitigation:** Not a risk — if plan mode hasn't been entered yet, `EnterPlanMode` should not
  be in the list. Enforcement (`intent_enforcement.py`) instructs the model to call it; the tool
  itself is always available from CC's Step 1 `<available-deferred-tools>` system prompt injection
  (74–80 tools). The Step 3 deferred cache path is the fallback, not the primary source.

- **Risk:** Plan sessions where Signal 0–3 all miss on turn 1 won't have the guarantee.  
  **Mitigation:** Signal 0 scans 120-message history (Fix 3, ADR-0010). Signal 3 persists
  `plan_mode_source` across proxy restarts. Signal 1 fires for CC-native `/plan`. Signal 2
  fires on PLAN intent from LLM classifier. Four independent signals make total miss extremely
  unlikely.

**Invariant confirmed:** IntentClassifier is registered before DeferredTools in
`build_request_pipeline()` (verified in `pipeline.py`). This ordering guarantee means
`ctx.plan_mode_active` is always populated when DeferredTools runs.
