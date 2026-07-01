# ADR-0012: Final Gate — Strip Plan-Only Tools from request.tools When Not in Plan Mode

**Status:** Accepted  
**Date:** 2026-06-30  
**Refs:** ADR-0011 (plan_guarantee gate), ADR-0010 (plan_mode_source), ADR-0008 (Signal 4)

---

## Context

After deploying Fix 6 (ADR-0011), a new plan-mode re-trigger was observed. Evidence from
session `91b342f7` (186 messages, cache clean: `plan_mode_active=False`, `deferred_tool_names=[]`):

```
tools_in=76  ← EnterPlanMode already in CC's request.tools before DeferredTools runs
[deferred-tools] INFO log: ABSENT  ← new_defs empty, tools already in existing_names
tools=ExitPlanMode,EnterPlanMode,Bash  ← from message HISTORY (Kimi already called them)
[stuck-loop-guard] Blocked 'EnterPlanMode' after 2 identical call(s)
```

The stuck-loop-guard loop:
```
Turn N:   Kimi → EnterPlanMode (1st call — allowed)
Turn N+1: Kimi → EnterPlanMode (2nd identical — blocked, guard resets)
Turn N+2: Kimi → Bash (different call — guard counter for EnterPlanMode resets)
Turn N+3: Kimi → EnterPlanMode (1st call after reset — allowed) → CC enters plan mode
```

### Root Cause

The deferred_tools.py docstring claims CC sends EnterPlanMode/ExitPlanMode **only** in the
`<available-deferred-tools>` system prompt block, not in `request.tools`. This assumption is
WRONG for some Claude Code project configurations. CC sends them natively in `request.tools`
(`tools_in=76` → `new_defs` empty → no injection log, no injection).

Fixes 1–6 only control DeferredTools **injection** paths (Steps 1–4). None of them touch the
tools already present in CC's `request.tools`. If CC sends EnterPlanMode natively, it's always
available to Kimi regardless of any injection gate.

---

## Decision

Add a **final gate** at the end of `DeferredToolsTransformer.transform()` that strips
`_PLAN_ONLY_TOOLS` (EnterPlanMode, ExitPlanMode) from `request.tools` when not in plan mode.

Gate conditions:
- `ctx.plan_mode_active=True` → **keep** (model needs ExitPlanMode)
- `ctx.intent == "PLAN"` → **keep** (model needs EnterPlanMode to enter plan mode)
- Otherwise (BUILD / READ / CHAT / VERIFY / SYNTHESIZING, `plan_mode_active=False`) → **strip**

**Placement:** Before early-return A (`if not deferred: return`) so the gate runs in ALL paths —
whether deferred list is empty, whether new_defs is empty, or after a successful injection.

**Change in `vendor/claude-code-proxy/llm/transformers/deferred_tools.py`:**

1. Move `_tool_name()` helper to module level (before the class, after constants).
2. Add final gate block before `if not deferred: return`:
   ```python
   _gate_active = not ctx.plan_mode_active and ctx.intent not in ("PLAN",)
   if _gate_active and request.tools:
       _before = list(request.tools)
       request.tools = [t for t in _before if _tool_name(t) not in _PLAN_ONLY_TOOLS]
       _stripped = [_tool_name(t) for t in _before if _tool_name(t) in _PLAN_ONLY_TOOLS]
       if _stripped:
           logger.info(
               "[deferred-tools] final-gate: stripped %d plan-only tool(s) "
               "(intent=%s): %s", len(_stripped), ctx.intent, ", ".join(_stripped),
           )
   ```
3. Remove inline `def _tool_name(t)` from inside `transform()` (now module-level).

---

## Consequences

**Positive:**
- EnterPlanMode/ExitPlanMode are structurally absent from request.tools during BUILD sessions.
- Kimi cannot call them (not in valid_names for passthrough extraction).
- The stuck-loop-guard never fires for plan tools (nothing to block).
- Works regardless of source: CC native, deferred injection, session cache restoration.

**Risks / Mitigations:**
- **Risk:** Plan mode entry blocked if first PLAN turn is misclassified as CHAT.  
  **Mitigation:** If intent=CHAT (not PLAN), enforcement doesn't ask for plan mode either.
  The user would rephrase and the classifier would return PLAN on the next turn.

- **Risk:** Intent=PLAN is a single-turn gate — on turn 2+ of entering plan mode, if the
  assistant turn is a tool_result (Override E: CHAT), the gate would strip EnterPlanMode.  
  **Mitigation:** On tool_result turns, enforcement injects the plan mode instruction in the
  system context; plan_mode_active is set from the history scan (Signal 0) by turn 2.

- **Invariant confirmed:** IntentClassifier sets `ctx.intent` before DeferredTools runs.
  `ctx.plan_mode_active` is also set by IntentClassifier. Both are available for the gate.
