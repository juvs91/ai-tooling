# ADR-0016: Native Tool-Use Structural Validation

**Status:** Accepted  
**Date:** 2026-07-13  
**Supersedes:** —  
**Superseded by:** —

---

## Context

Kimi K2 in passthrough mode (`PASSTHROUGH_DISABLED=0`) returns native Anthropic-format `tool_use` blocks directly. Under concurrent load (3+ parallel agents), these blocks arrive with missing or incorrectly typed structural fields — most commonly `id` (null or absent) and `input` (null instead of a dict). Claude Code's SDK rejects these with:

```
"The model's tool call could not be parsed (retry also failed)"
```

This produced a 67% crash rate in parallel agent sessions.

The existing `ToolCallValidatorTransformer` (RC-5) is already in the Kimi K2 response path but only validates 7 specific deferred tools via `_CORRECTORS`. Any native `tool_use` block with `name = "Read"`, `"Write"`, `"Agent"`, etc. passes without structural inspection.

Additionally, no few-shot examples of the native Anthropic tool_use format are injected for Kimi K2 passthrough requests — only XML `<tool_call>` examples exist for no-tools models.

## Decision

### 1. New module: `structural_tool_validator.py`

A dedicated module alongside `tool_call_validator.py` that holds:
- `STRUCTURAL_VALIDATORS`: declarative dict of `(predicate_fn, fixer_fn)` keyed by field name
- `apply_structural_validation(block)`: applies all rules, returns correction messages
- `record_malformed_block(block, corrections)` + `pop_malformed_blocks()`: in-memory store for retry analysis
- `build_correction_prompt(blocks)`: few-shot native Anthropic format correction prompt

### 2. Extend `ToolCallValidatorTransformer.transform()`

Run structural validation on ALL `tool_use` blocks before the tool-specific correctors. Records malformed blocks for retry and appends `"structural:<tool_name>"` to `ctx.quality_issues`.

### 3. Retry-with-few-shot in `proxy.py`

After `_run_response_pipeline()` in the passthrough non-streaming path, if `ctx.quality_issues` contains structural markers and this is the first retry attempt, make one correction call to Kimi K2 with the malformed response as assistant turn + few-shot correction prompt. Falls back to auto-patch if the retry call fails.

### 4. Per-provider concurrency semaphore (defense-in-depth)

`asyncio.Semaphore` keyed by provider base URL wraps `pt.create_message()` in the passthrough path. Configurable via `MAX_CONCURRENT_PER_PROVIDER` env var (default: 5, Kimi K2 profile: 2).

## Consequences

**Positive:**
- Centralized structural responsibility in `ToolCallValidatorTransformer` / `structural_tool_validator.py`
- Deterministic auto-patch fallback (prevents SDK crash even if retry fails)
- Few-shot correction gives Kimi K2 a chance to produce valid output rather than guessing format
- Semaphore reduces concurrent load that triggers malformation in the first place
- Hot-reload applies automatically (no rebuild needed)

**Trade-offs:**
- Retry adds one extra roundtrip to Kimi K2 when structural issues are detected (only triggered, not on every call)
- `MALFORMED_STORE` is in-memory; cleared on process restart (acceptable — used for same-session retry only)

## Files Changed
1. `llm/transformers/structural_tool_validator.py` (new)
2. `llm/transformers/tool_call_validator.py` (extend transform loop)
3. `proxy/proxy.py` (retry + semaphore in passthrough non-streaming path)
4. `config.py` (`max_concurrent_per_provider` field)
5. `profile-envs/cloud.kimi-coding.env` (`MAX_CONCURRENT_PER_PROVIDER=2`)
