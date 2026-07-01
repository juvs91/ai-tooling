# ADR-0010: Plan Mode Source Tracking + Signal 4 Scope + Classifier Gate Fix

**Status**: Accepted  
**Date**: 2026-06-30  
**Author**: juvs  
**Context**: Kimi K2 proxy (`cloud.kimi-coding.env`) re-triggers `EnterPlanMode` ~every 60 turns

---

## Problem

Sessions using Kimi K2 (`cloud.kimi-coding.env`) oscillate between plan mode and normal mode:

1. **`_models_differ = False`** (all three routing models are `kimi-k2`): `server.py:74-77`
   compares only routing models → LLM classifier (DeepSeek-Chat) is silently skipped
   → BUILDING_RE regex fallback is the sole classifier for all turns.

2. **BUILDING_RE false positives**: regex matches broad terms (`error`, `schema`, `bash`,
   `endpoint`) in natural-language plan-session messages → returns BUILD instead of PLAN.

3. **Signal 4 (ADR-0008) fires on false-positive BUILD**: Signal 4 was designed for the
   CC `/plan` → Autoedit UI transition, where Signal 1 (`"Plan mode is active"` in system
   prompt) goes from True → False. For proxy-initiated plan mode (Kimi enforcement path),
   Signal 1 is **never present** — making Signal 4 fire on every false-positive BUILD,
   poisoning the session cache.

4. **Cache poison → oscillation**: after Signal 4 clears `plan_mode_active`, Signal 0
   recovers it (EnterPlanMode in last 60 msgs). But once EnterPlanMode is outside the
   60-message window, Signal 0 fails, Signal 3 (poisoned cache) is False → enforcement
   re-triggers EnterPlanMode. Period ≈ 60 turns.

---

## Decision

### Fix 1 — Enable LLM classifier whenever CLASSIFIER_MODEL is set

`server.py:74-77`: `_models_differ` currently compares only routing models.
When `CLASSIFIER_MODEL` is explicitly configured, the operator intends accurate
intent classification (plan mode enforcement depends on it) regardless of routing uniformity.

```python
_routing_differs = (
    cfg.routing.big_model != cfg.routing.small_model
    or cfg.routing.building_model != cfg.routing.big_model
)
_models_differ = _routing_differs or bool(cfg.classifier.model and cfg.classifier.api_key)
```

### Fix 2 — `plan_mode_source` in session cache (foundation for Fix 3)

Add `plan_mode_source: str | None` and `plan_mode_events: list[dict]` to `_CompressionCache`.

Source lifecycle:
- Signal 1 fires → `plan_mode_source = "cc"`
- Signal 2 fires (no Signal 1) → `plan_mode_source = "proxy"`
- Signal 0 fires with source=None in cache → infer: "cc" if Signal 1 seen in recent msgs, else "proxy"
- ExitPlanMode detected → `plan_mode_source = None` (reset for next planning session)

### Fix 3 — Signal 4 scoped to CC-initiated plan mode

```python
_pm_source = await get_session_plan_mode_source(ctx.session_id)
if (plan_mode_active
        and "Plan mode is active" not in _system_text
        and ctx.intent in ("BUILD", "VERIFY")
        and _pm_source == "cc"):   # proxy-initiated → never fires Signal 4
    plan_mode_active = False
```

ADR-0008 behavior is preserved for CC-initiated sessions. Proxy-initiated sessions
are immune to Signal 4 and exit plan mode only via explicit ExitPlanMode tool call.

### Fix 4 — Window 60 → 120 (Signal 0, deferred_tools, intent_enforcement)

Doubles the history coverage before Signal 3 (cache) becomes the sole source of truth.
Reduces the frequency at which cache poisoning matters.

---

## Consequences

- **Kimi sessions**: No more plan mode oscillation. LLM classifier (DeepSeek-Chat) now
  runs, reducing false-positive BUILD classifications. Signal 4 never fires for proxy-initiated
  plan mode. ExitPlanMode is the only exit gate.
- **CC /plan sessions**: Behavior unchanged — Signal 4 still fires when CC UI switches mode.
- **New fields in session cache**: Backward-compatible (default values on load). Disk cache
  format adds `plan_mode_source` and `plan_mode_events` fields.
- **Observability**: `plan_mode_events` accessible via `/api/stats` — shows exactly when
  plan mode entered, when Signal 4 was blocked, and when it exited cleanly.
- **Latency**: Fix 1 adds ~1-15s (classifier timeout) for first-turn classification on
  single-model configs. Subsequent turns use cached classification state. Acceptable
  trade-off vs current oscillation.
