# ADR-0021: ANALYSIS_MODEL requires provider prefix + ModelRouter hardening

**Status:** Accepted  
**Date:** 2026-07-14  
**Refs:** `llm/transformers/model_router.py:70`, `profile-envs/cloud.kimi-coding.env:20`

---

## Context

When `analysis_phase == "SYNTHESIZING"`, `ModelRouterTransformer` sets:
```python
request.model = self._analysis.model   # = ANALYSIS_MODEL env var
```

With `ANALYSIS_MODEL=kimi-k2` (bare, no provider prefix) and `PASSTHROUGH_REQUIRE_PREFIX=1`:
- `_is_passthrough_compatible("kimi-k2")` returns `False` (no `anthropic/` prefix)
- Request falls to LiteLLM pipeline
- `ProviderQuirksTransformer` injects thinking params → `analysis_thinking (generic fallback)`
- LiteLLM: `"LLM Provider NOT provided. You passed model=kimi-k2"`
- Falls to DeepSeek fallback → **DeepSeek synthesizes instead of Kimi**

**Consequences observed:**
1. Every SYNTHESIZING turn goes to DeepSeek (5 fallbacks in session logs)
2. Quality score 0.40 → REFINE triggered, but refinement re-request also uses bare `kimi-k2` → same failure → `is_stream=True` from fallback → early return → `ctx.refinement_attempt` never incremented → `analysis_refinements: 0`
3. `analysis_thinking (generic fallback)` log on every SYNTHESIZING turn

---

## Decision

**Immediate fix (env file):** Add `anthropic/` prefix to `ANALYSIS_MODEL` in `cloud.kimi-coding.env`:
```diff
-ANALYSIS_MODEL=kimi-k2
+ANALYSIS_MODEL=anthropic/kimi-k2
```

**Hardening fix (model_router.py):** When `self._analysis.model` lacks a provider prefix, apply
`build_model_name(self._routing.preferred_provider, self._analysis.model)` automatically.
This prevents the same issue from recurring if another env file omits the prefix.

```python
# BEFORE (line 70):
request.model = self._analysis.model

# AFTER:
from router.model_mapper import has_provider_prefix, build_model_name
if has_provider_prefix(self._analysis.model):
    request.model = self._analysis.model
else:
    request.model = build_model_name(
        self._routing.preferred_provider, self._analysis.model
    )
```

---

## Files Changed

1. `profile-envs/cloud.kimi-coding.env` — `ANALYSIS_MODEL=anthropic/kimi-k2` (immediate fix)
2. `vendor/claude-code-proxy/llm/transformers/model_router.py` — prefix guard (hardening)

---

## Consequences

**Positive:**
- SYNTHESIZING calls route to Kimi via passthrough (not DeepSeek fallback)
- `analysis_refinements` counter now increments correctly
- `analysis_thinking (generic fallback)` log eliminated for SYNTHESIZING phase
- Quality refinement loop completes with the correct model

**Negative:**
- Slight code change in model_router to handle the prefix check

**Env file change takes effect on next container restart (hot-reload not sufficient for env var changes).**
