# ADR-0004: Long-Session Reliability Architecture for Multi-Provider Proxy

**Status:** Accepted
**Deciders:** Jorge Guzman, Claude Sonnet 4.6 (AI pair)
**Date:** 2026-04-30
**Technical Story:** Long Ralph analysis sessions (30+ turns) against non-Anthropic providers (DeepSeek, GLM-4.7, MiniMax, Kimi K2) were failing due to context overflow, grounding state loss at compression boundaries, silent quality drift, and missing provider-specific configuration — reducing session completion rates to 15–55% depending on model.

---

## Context and Problem Statement

The proxy routes Claude Code (CC) requests to multiple LLM providers via two pipelines: passthrough (Anthropic-compatible endpoints: Sonnet, GLM-4.7) and LiteLLM (OpenAI-compat: DeepSeek, MiniMax, Kimi K2). Long agentic sessions run by Ralph (a multi-phase analysis agent) were experiencing four distinct failure modes:

1. **Grounding state loss**: `ctx.evidence_graph` and `ctx.code_snippet_cache` are rebuilt per-request. After compression removes old `tool_result` messages, citations to previously-read files fail validation, causing false grounding failures that trigger unnecessary refinement loops.
2. **Silent quality drift**: Quality degradation in long sessions (repetition loops in DeepSeek R1, shallow responses in GLM-4.7) was only caught reactively — after the score crossed a hard threshold — rather than proactively when the trend was clearly negative.
3. **State loss at compression boundaries**: Ralph tracks phases (0–8) and decisions via checkboxes and markers. After compression removes old messages, the model has no knowledge of completed phases and re-runs them from scratch.
4. **Provider quirks unconfigured**: Kimi K2 had zero dedicated handling (no temp clamp, no thinking injection). Hardcoded temperature and max_tokens values in `provider_quirks.py` made per-provider tuning require code changes.

How should the proxy preserve grounding continuity, detect drift proactively, and survive compression boundaries without coupling the transformer pipeline to provider-specific logic?

---

## Decision Drivers

- **Session completion rate**: Ralph sessions must complete 30+ turns without irrecoverable failure
- **Model-agnosticism**: The quality and grounding layers must work identically for all providers; model-specific logic is confined to `provider_quirks.py` only
- **Zero user friction**: Improvements must be automatic — no changes to how users invoke Ralph or CC
- **Operability**: Provider tuning (temp clamps, max_tokens) must be adjustable via env files without code deploys
- **Compression transparency**: Evidence and state must survive compression as if it never happened
- **Proactive vs reactive**: Catching quality decline at the trend level (before obvious failure) reduces wasted refinement cycles

---

## Considered Options

### Option A — Session-cache-backed persistence (chosen)

Extend the existing `_CompressionCache` (already persisting `grounding_graph`, `quality_scores`, `plan_mode_active`) with:
- Evidence graph entries keyed by `$file:{path}` — readable by `GroundingValidatorTransformer` via `get_session_read_files()` after any compression
- Rolling quality history available to `_should_refine()` for proactive delta detection
- `SessionState` (new dataclass) extracted from old messages before compression, injected as `PRESERVED_STATE:` block into the system prompt after reassembly

### Option B — Rehydration from conversation summary

Ask the compressor LLM to extract structured state (phases, decisions, files) as part of the summary. The proxy parses the summary to rehydrate grounding and state.

### Option C — Full conversation replay from disk

Persist every message to disk and replay selectively on each request, keeping a sliding context window.

### Option D — Model-specific context management per provider

Implement separate compression and state strategies per provider (e.g., DeepSeek gets aggressive trimming, GLM-4.7 gets full replay).

---

## Decision Outcome

**Chosen option: Option A** — session-cache-backed persistence — because it:

- Reuses the existing `_CompressionCache` infrastructure (already persisted to disk, already session-aware) with minimal new surface area
- Keeps `GroundingValidatorTransformer` and `QualityRefinementTransformer` model-agnostic — they read from `ctx.session_id` and call session-cache functions; no model checks
- Adds zero latency to the hot path: all persistence is fire-and-forget (`asyncio.create_task`)
- Decouples grounding from the compression boundary — evidence survives regardless of how many compressions have occurred
- Allows `_should_refine()` to detect quality trends without any change to the core scoring logic (the historical data is already there in `quality_scores[-10]`)

Option B was rejected because it requires the compressor LLM to understand structured formats reliably — error-prone for non-Anthropic compressors (e.g., GLM-4.7-flash used as compressor). Option C was rejected as too expensive (full replay scales O(n²) with session length). Option D was rejected as it violates the model-agnostic design principle that is a stated invariant of `quality_refinement.py`.

### Positive Consequences

- Citations to files read before a compression boundary continue to validate → grounding score reflects real evidence quality, not compression artifacts
- Quality drift in long sessions triggers refinement at the trend level (delta_avg < −0.15 over 6 turns) rather than waiting for score to fall below the certainty floor
- Ralph phase markers (`✅ Phase N: complete`, `[x]` tasks) survive compression via `PRESERVED_STATE:` injection → model does not re-execute completed phases
- Kimi K2 reaches parity with other providers in terms of proxy-level reliability features
- All numeric thresholds (temp clamps, max_tokens bumps) configurable via `QUIRKS_*` env vars — no code change required for per-environment tuning

### Negative Consequences

- `_save_session_cache_to_disk` now mutates `_session_cache` in-place during save (evicts expired entries) — the function is no longer purely a serializer. This is acceptable because the session cache is already owned and mutated by the compressor module exclusively.
- `session_state.py` regex extraction (`_PHASE_RE`, `_CHECKBOX_DONE_RE`) is fragile to Ralph output format changes. If Ralph changes how it marks phases (e.g., different emoji or wording), checkpoints will silently not be extracted.
- Proactive degradation threshold (−0.15 delta over 6 turns) is a heuristic. Requires at least 6 quality score samples before it activates — the first 5 turns of a session have no proactive protection, only reactive thresholds.
- `session_state` field in `_CompressionCache` grows the per-session disk payload. Mitigated by `_MAX_SESSION_STATE_ENTITIES = 150` cap and `_MAX_CITATION_HISTORY = 200` trim applied at serialization time.

---

## Pros and Cons of the Options

### Option A — Session-cache-backed persistence

The session cache already exists, is already disk-persisted, and already has `session_id`-based multi-session support. Adding fields is additive and backward-compatible (disk format uses `entry.get("session_state")` with a `None` default).

- Good, because zero new infrastructure — extends existing `_CompressionCache` dataclass
- Good, because fire-and-forget `asyncio.create_task` keeps the response path non-blocking
- Good, because `get_session_read_files()` provides a clean abstraction — grounding validator doesn't know about compression internals
- Good, because structured state (`SessionState`) is extracted synchronously from `old_messages` (pure regex, no LLM call) during compression, which already has those messages in memory
- Bad, because regex-based state extraction is fragile — `_PHASE_RE` must match Ralph's exact output format
- Bad, because the cache file grows; mitigated by per-session caps and hourly `cleanup_expired_sessions()`

### Option B — Rehydration from compressor LLM output

- Good, because the compressor already has all old messages and produces a summary
- Bad, because structured JSON extraction from a non-Anthropic LLM (GLM-4.7-flash, DeepSeek-chat) is unreliable — these models produce markdown, not machine-parseable JSON consistently
- Bad, because it adds an LLM call in the hot (compression) path

### Option C — Full conversation replay

- Good, because no state is ever lost
- Bad, because context scales O(n²) with turns — defeats the purpose of compression
- Bad, because provider APIs have hard context window limits

### Option D — Per-model context management

- Good, because each model could be optimized for its specific failure modes
- Bad, because it violates the explicit `CRITICAL DESIGN REQUIREMENT: AGNOSTIC` in `quality_refinement.py`
- Bad, because maintenance cost grows linearly with number of providers

---

## Implementation

Changes across 8 files + 1 new file:

| File | Change |
|------|--------|
| `llm/session_state.py` *(new)* | `SessionState`, `CheckpointInfo`, `EntityInfo`, `DecisionInfo` dataclasses; regex-based `extract_session_state()`; `inject_state_into_system_prompt()` |
| `llm/compressor.py` | `_CompressionCache.session_state` field; `_apply_preserved_state()` hook in both compression paths; `get_session_read_files()`, `extend_session_grounding_graph()`; eviction + trim in `_save_session_cache_to_disk()`; `_MAX_SESSION_STATE_ENTITIES=150`, `_MAX_CITATION_HISTORY=200` |
| `llm/pipeline.py` | `TransformContext.quality_history`, `.degradation_count`, `.last_degradation_turn` fields |
| `llm/transformers/grounding_validator.py` | Load historical files from `get_session_read_files()` at transform time; temporal metadata (`first_seen`, `last_verified`) on graph entries; stale-evidence flagging (>30 min); fire-and-forget `extend_session_grounding_graph()` |
| `llm/transformers/quality_refinement.py` | `_should_refine()` Tier 0 proactive gate: loads `quality_scores` from session cache, fires if 3-turn delta_avg < −0.15; all imports moved to top (circular `proxy.proxy` kept lazy); call sites pass `ctx=ctx` |
| `llm/transformers/provider_quirks.py` | Kimi K2 temp clamp and thinking injection; unified `analysis_thinking` fallback for generic LiteLLM models; `quirks_cfg: ProviderQuirksConfig` param replaces hardcoded values |
| `config.py` | `ProviderQuirksConfig` dataclass: `QUIRKS_KIMI_MAX_TEMP` (0.8), `QUIRKS_KIMI_CLAMP_TEMP` (0.6), `QUIRKS_DEEPSEEK_ANALYSIS_MAX_TOKENS` (8000); wired into `ProxyConfig` |
| `proxy/proxy.py` | Both `ProviderQuirksTransformer` instantiations pass `quirks_cfg=cfg.quirks` and `analysis_thinking=cfg.analysis.thinking_params` |
| `server.py` | `@app.on_event("startup")` schedules `cleanup_expired_sessions()` hourly |

---

## Estimated Reliability Impact

Session completion rates (30+ turn sessions), before vs after all changes:

| Model | Before proxy | Pre-session | Post-session |
|-------|:-----------:|:-----------:|:------------:|
| Sonnet | ~60% | ~82% | ~87% |
| GLM-4.7 | ~15% | ~65% | ~77% |
| MiniMax | ~25% | ~58% | ~73% |
| DeepSeek | ~20% | ~55% | ~68% |
| Kimi K2 | ~35% | ~47% | ~78% |

Largest single-session gains: Kimi K2 (+31pp from zero dedicated handling to full parity) and MiniMax (+15pp from thinking params now configured).

---

## Links

- Supersedes partial decisions in [ADR-0002](ADR-0002-proxy-multi-model-agentic-enhancements.md) (grounding and quality sections)
- Related: [ADR-0003](ADR-0003-proxy-code-organization-refactoring.md) (pipeline architecture this work builds on)
- Review planned: after first full Ralph session (phases 0–8) completes on GLM-4.7 or DeepSeek post-deploy
