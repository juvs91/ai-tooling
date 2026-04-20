# ADR-0002: Proxy Multi-Model Agentic Enhancements

- **Date**: 2026-04-16
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

The proxy supports GLM-4.7, MiniMax M2.5, Kimi K2, DeepSeek Chat, and Sonnet 4.6 as
agentic coding models via Claude Code. Analysis of the proxy transformer pipeline against
4 scenarios (CC+human, CC+proxy+human, CC+ralph, CC+proxy+ralph) reveals:

1. **Tool validator covers only AskUserQuestion** — ExitPlanMode, EnterPlanMode, TodoWrite,
   WebFetch, NotebookEdit have no schema validation or auto-correction, causing silent
   failures and Ralph circuit breaker triggers.

2. **Ralph sessions have no mode signal** — the proxy cannot distinguish autonomous Ralph
   loops from human CC sessions, so it cannot suppress AskUserQuestion calls that have no
   one to answer them.

3. **Refinement prompts are generic** — "please improve" regardless of which heuristic
   failed (H18 stubs, H7 unverified claims, H6 shallow exploration, grounding score low).
   Targeted prompts would make each refinement surgical.

4. **Session quality degrades silently** — the proxy has no cross-turn quality memory.
   A model that produces 3 stub-filled responses in a row receives the same enforcement
   on turn 4 as on turn 1. SessionCache already persists plan mode and grounding state
   but not quality history.

5. **MiniMax grounding graph expires too fast** — GROUNDING_GRAPH_MAX_ENTITIES=100 and
   GROUNDING_GRAPH_PRUNE_AGE=600s are calibrated for 128K-context models. MiniMax's 1M
   context accumulates far more entities in a session; both caps need raising.

6. **Kimi K2 has no provider profile** — cannot be evaluated without configuration.

---

## Decision

### 1. RALPH_MODE via system prompt marker (zero coupling)

Ralph injects `PROXY_SESSION_MODE: ralph` into the CC system prompt via
`--append-system-prompt`. The proxy detects this marker in `IntentClassifierTransformer`
and sets `ctx.ralph_mode = True`. `IntentEnforcementTransformer` then injects:

```
RALPH MODE (autonomous): Do NOT call AskUserQuestion — no human is present.
Make best-effort decisions. Document assumptions in the plan file instead of asking.
```

**Why this approach**: CC is a black box — it does not propagate custom HTTP headers.
The system prompt is the only in-band channel that passes through CC unmodified. No
shared state files, no API polling, no coupling between Ralph and proxy.

### 2. Full deferred tool validator (7 tools)

Extend `ToolCallValidatorTransformer` in `tool_call_validator.py` to validate and
auto-correct all 7 deferred tools. Each correction is logged and counted in metrics
as `tool_corrections_count` for observability.

| Tool | Correction |
|------|-----------|
| `ExitPlanMode` | Strip all params → `{}` |
| `EnterPlanMode` | Strip all params → `{}` |
| `AskUserQuestion` | Already handled — extend existing logic |
| `TodoWrite` | Wrap flat `content` string → `todos[{content,status,priority}]` |
| `WebSearch` | Ensure `query` is a non-empty string |
| `WebFetch` | Ensure `url` present; inject `prompt` default if missing |
| `NotebookEdit` | Ensure required fields present |

### 3. Targeted refinement by heuristic + raise default max_refinements

`quality_refinement.py` will pass the list of triggered heuristics to the refinement
prompt builder, replacing the generic "please improve" with a surgical instruction:

- H18 stubs → "Implement full logic for: {stub_locations}"
- H7 unverified claims → "Read and verify: {unverified_files}"
- H6 shallow → "You read {n_read} of {n_mentioned} files. Read the rest."
- Grounding low → "Citations not found in read history. Read: {missing}"

Default `ANALYSIS_MAX_REFINEMENTS` raised from 1 → 2 in non-Sonnet profiles.

### 4. Session-level quality adaptation (proxy-internal, no external coupling)

Extend `_CompressionCache` (SessionCache) with:
- `quality_scores: list[float]` — last N quality scores for this session
- `session_stub_count: int` — total H18 stubs detected in session

`quality_refinement.py` persists scores to session after each response.
`intent_enforcement.py` reads session history at request time and injects escalating
enforcement when avg quality < 0.55 or stub_count ≥ 2.

**Why proxy-internal**: Ralph must not depend on proxy APIs or state files. The proxy
already manages session state (plan mode, deferred tools, grounding graph) — quality
history is a natural extension of the same cache.

### 5. MiniMax config tuning + Kimi K2 profile

`cloud.mixed-router.env`: raise `GROUNDING_GRAPH_MAX_ENTITIES` 100→500,
`GROUNDING_GRAPH_PRUNE_AGE` 600→1800, `ANALYSIS_MAX_REFINEMENTS` 1→2.

`cloud.kimi.env` (new): Moonshot AI provider profile for Kimi K2.

---

## Consequences

**Positive:**
- Tool validator eliminates silent failures that trigger Ralph circuit breakers (+3% ralph est.)
- RALPH_MODE suppresses dead-end AskUserQuestion calls in autonomous loops (+2% ralph est.)
- Targeted refinement makes each retry surgical instead of blind (+2% all models est.)
- Session quality memory escalates enforcement when model degrades (+4% ralph est.)
- MiniMax grounding tuning reduces drift in long sessions (+3% MiniMax est.)

**Negative / Trade-offs:**
- `_CompressionCache` grows slightly — two new fields serialized to disk cache
- Refinement targeted prompts require heuristic IDs to be passed through quality pipeline
  (minor coupling between quality scorer and refinement prompt builder)
- RALPH_MODE detection adds one string scan per request to IntentClassifierTransformer
  (negligible performance impact)

---

## Files Modified

| File | Change |
|------|--------|
| `llm/transformers/tool_call_validator.py` | Extend to all 7 deferred tools |
| `llm/transformers/intent_classifier.py` | Detect PROXY_SESSION_MODE marker |
| `llm/transformers/intent_enforcement.py` | Inject RALPH_MODE rules; session quality escalation |
| `llm/transformers/quality_refinement.py` | Targeted refinement prompts; persist scores to session |
| `llm/compressor.py` | Extend _CompressionCache with quality_scores, session_stub_count |
| `profile-envs/cloud.mixed-router.env` | Grounding + refinement config tuning |
| `profile-envs/cloud.kimi.env` | New Kimi K2 provider profile |
| `.ralph/claude-ralph.md` | Add PROXY_SESSION_MODE: ralph marker |
