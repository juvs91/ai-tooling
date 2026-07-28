# ADR-0032: Decompose `compressor.py` into a `session/` Package

**Status:** Accepted
**Date:** 2026-07-27
**Supersedes:** —
**Superseded by:** —

---

## Context

`llm/compressor.py` grew to 1487 lines and ~42 top-level definitions. It started as
"the module that compresses long conversations," but every session-scoped feature
added since (`plan_mode`, `deferred_tools` cache, `quality_history`,
`grounding` graph, and now `completion_claims` from ADR-0031) reused the same
`_CompressionCache`/`_session_cache` as a general-purpose session KV-store, not
specifically a compression cache. The file no longer reflects its own name and each
new session-state feature makes it larger and harder to navigate.

Confirmed before deciding the split:
- The 11 message/token manipulation helpers (`estimate_tools_tokens`,
  `_serialize_messages_for_summary`, `_normalize_messages`, `_detect_tool_inflation`,
  `_find_safe_split_point`, `_split_conversation`, `_trim_by_token_budget`,
  `_enforce_message_cap`, `_validate_tool_references`, `_fix_orphan_tool_messages`,
  `_needs_xml_reinforcement`) touch neither `_session_cache` nor `_state_lock` —
  verified by grep over their full line range. They are pure functions.
- 13 external imports (`from llm.compressor import ...`) across 10 files
  (`intent_classifier.py`, `grounding_validator.py`, `guardrail.py`,
  `quality_refinement.py`, `deferred_tools.py`, `intent_enforcement.py`,
  `compression.py`, `stream_event.py`, `server.py`, `proxy/proxy.py`). Updating all of
  them would make this a much larger, riskier change than the "mini refactor" asked
  for.
- The core compression algorithm (`compress_messages_if_needed`) genuinely depends on
  session-lifecycle (`get_or_create_session`/`update_session`) and grounding
  (`_prune_grounding_graph`, fire-and-forget) — it is not fully decoupled from
  session state, just narrower in scope than the current file.

## Decision

Split into a new `llm/session/` package (one module per concern) plus
`utils/message_utils.py` (pure message helpers), keep `llm/compressor.py` as a facade:
it retains only the actual compression algorithm (7 definitions:
`compress_messages_if_needed`, `_apply_preserved_state`, `_llm_compress_single`,
`_llm_compress`, `_reassemble_with_summary`, `_reassemble_trimmed`,
`log_compaction`) and re-exports everything moved out, so the 13 external call sites
require zero changes.

Module split (exhaustive, one row per current top-level definition):

| Module | Contents |
|---|---|
| `llm/session/store.py` | `_CompressionCache`, `_session_cache`, `_state_lock`, TTL/size constants, `_compute_prefix_hash`, `_save_session_cache_to_disk`, `_load_session_cache_from_disk` |
| `llm/session/plan_mode.py` | `get/set_session_plan_mode`, `get/set_session_plan_mode_source`, `get_session_plan_mode_events` |
| `llm/session/completion_claims.py` | `append/get_session_completion_claims` (ADR-0031) |
| `llm/session/quality_history.py` | `get_session_quality_history`, `append_session_quality` |
| `llm/session/deferred_tools_cache.py` | `get/save_session_deferred_tools` |
| `llm/session/grounding.py` | `get_session_grounding_graph`, `extend_session_grounding_graph`, `get_session_read_files`, `_track_grounding_hop`, `_prune_grounding_graph`, `get_grounding_state` |
| `llm/session/lifecycle.py` | `get_or_create_session`, `update_session`, `cleanup_expired_sessions` |
| `utils/message_utils.py` | the 11 pure message/token helpers |
| `llm/compressor.py` (stays) | the 7 compression-algorithm definitions + re-exports |

`_CompressionCache` is not split further — it is the single shared state container
every session-feature module reads/writes through `_session_cache`/`_state_lock`,
both imported from `store.py` (never redefined) so mutation stays visible across
modules.

Explicitly deferred: updating the 13 external call sites to import directly from
`llm.session.*` instead of the `llm.compressor` facade. That is a separate,
non-"mini" follow-up if ever wanted — no behavior changes here, pure reorganization.

## Consequences

- `llm/compressor.py` shrinks from 1487 lines to roughly its 7-function core +
  re-export block.
- New session-state features (the next one after `completion_claims`) get an obvious
  home (`llm/session/<concern>.py`) instead of accumulating in `compressor.py`.
- Zero behavior change — pure code motion, same shared `_session_cache` object, same
  disk-persistence format.
- The facade re-export block in `compressor.py` is the one piece of "impurity" this
  design accepts in exchange for not touching 10 other files.

## Files Changed

- `vendor/claude-code-proxy/llm/session/__init__.py` — new
- `vendor/claude-code-proxy/llm/session/store.py` — new
- `vendor/claude-code-proxy/llm/session/plan_mode.py` — new
- `vendor/claude-code-proxy/llm/session/completion_claims.py` — new
- `vendor/claude-code-proxy/llm/session/quality_history.py` — new
- `vendor/claude-code-proxy/llm/session/deferred_tools_cache.py` — new
- `vendor/claude-code-proxy/llm/session/grounding.py` — new
- `vendor/claude-code-proxy/llm/session/lifecycle.py` — new
- `vendor/claude-code-proxy/utils/message_utils.py` — new
- `vendor/claude-code-proxy/llm/compressor.py` — rewritten as facade + core algorithm

## Verification

- Full proxy suite (`pytest tests/ -q`) — must stay at the pre-refactor baseline
  (`1197 passed, 0 failed`), confirming zero behavior change.
- Direct import check for all 10 files with external `llm.compressor` imports.
- `_session_cache`/`_state_lock` confirmed to be the same shared object across
  `llm.session.*` modules (write via one module, read via another in the same test).
