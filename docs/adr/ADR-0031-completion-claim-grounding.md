# ADR-0031: Detect Ungrounded Completion Claims

**Status:** Accepted
**Date:** 2026-07-27
**Supersedes:** —
**Superseded by:** —

---

## Context

Evaluating two real Kimi K2 sessions (school-system: React-hooks bug analysis;
track_trace_data_extractor: Go/UNIGIS exploration) found that Kimi doesn't only
fabricate findings — it fabricates having **resolved** them. In a final summary turn
it claimed: *"ya cerré: useToast listener leak, useSchedules migración a useApiData,
duplicados use-toast/use-mobile"* — none of those three were real problems to begin
with (independently verified against the current source), and the one real bug that
did exist (`hooks/use-bulk-grades.ts`, a double-counted `failureCount` on total submit
failure) was never touched.

`GroundingValidatorTransformer` (`llm/transformers/grounding_validator.py`) already
validates **citations** — was the cited file actually read? — but has no notion of
**completion claims**: "the model says it fixed X" is never cross-checked against
"did an Edit/Write/MultiEdit actually target X anywhere in this conversation."

Two adjacent, pre-existing mechanisms were checked and confirmed NOT to already cover
this (avoiding duplication):
- `verified_claims`/`grounding_graph` multi-hop tracking (`llm/compressor.py:1344-1347`)
  — built for the `ticket-implementation` skill's 7-hop grounding, tracks
  entity→entity relationships, not "I fixed X" claims.
- `unverified_claims` heuristic H7 (`utils/quality.py:161-168`) — fires when a
  response has >5 factual claims with zero tool calls in that turn. A general
  no-evidence-at-all signal, not specific to a completion claim referencing a file
  that was never edited.

## Decision

Add completion-claim detection to `GroundingValidatorTransformer`, using a **dual
signal** design, both persisted through the existing session-cache infrastructure
(`_CompressionCache` in `llm/compressor.py`, same pattern as `plan_mode_events`):

1. **Strong signal — a delimited text block, not a new tool.** A synthetic Claude-Code
   tool (e.g. `ReportTaskCompletion`, mirroring how `deferred_tools.py` injects
   `EnterPlanMode`/`AskUserQuestion`) was considered and rejected: those are tools
   Claude Code's own client already knows how to execute. A proxy-invented tool the
   client doesn't recognize would break the conversation (client receives a
   `tool_use` for something it can't handle). Instead, the proxy instructs the model
   (system/policy note) to append a delimited ` ```task-completion\n{...}\n``` ` JSON
   block when it believes a fix is complete. This is ordinary text from Claude Code's
   perspective — nothing breaks client-side — and the proxy strips it from the
   response before forwarding (same extract-and-clean pattern already used by
   `universal_tool_extraction.py` for XML tool calls).
2. **Weak signal — regex fallback**, only evaluated when no structured block is
   present. Covers the actual Kimi failure mode (no structured cooperation at all,
   just prose "ya cerré X"). Marked with lower severity given free-text pattern
   matching is inherently more fragile than a fixed delimited format.

Both signals are cross-checked against `_extract_all_edited_paths(messages)` — every
`Edit`/`Write`/`MultiEdit` target path across the **whole conversation**, not just the
current turn. This is deliberate: the real Kimi case was a completion claim made in a
final summary turn, referencing edits (real or fabricated) from earlier turns —
restricting to the current turn's own edits would have missed exactly that case.

Explicitly deferred to a later phase (not built now):
- Semantic content verification ("does what it says about the file match the file's
  real content?") — requires full-content caching + diffing, not just edit-presence.
  This ADR only closes the "claimed fix, zero corresponding edit" gap.
- A `Stop`-hook end-of-session audit reading the accumulated `completion_claims` —
  deferred because the real-time system-note (same turn) already provides value
  without it.
- Wiring into the quality-refinement re-generation loop (`quality_refinement.py`) —
  that pipeline is currently scoped to `is_analysis` responses; the observed failure
  was in a BUILD-intent context, so extending it needs its own investigation.

## Consequences

- A model claiming a fix with no backing edit now gets an immediate, same-turn
  system-note nudge (via `ensure_system_note`, the same helper already used for
  "editing without prior Read") — a self-correction opportunity, no forced
  re-generation loop.
- Claims and their verification outcome persist per-session (`completion_claims` on
  `_CompressionCache`, capped like `plan_mode_events`), available for a future
  end-of-session audit without needing to rebuild the collection mechanism then.
- Residual: whole-conversation edit scanning has no recency bound yet — an edit from
  many turns ago in an unrelated context still counts as "backing" for a claim now.
  Noted as a future refinement (track `last_verified_turn` per file, mirroring
  `evidence_graph`'s existing `last_verified` timestamp), not blocking for this phase.

## Files Changed

- `vendor/claude-code-proxy/llm/pipeline.py` — new `TransformContext` field
  `unverified_completion_claims`.
- `vendor/claude-code-proxy/llm/compressor.py` — new `_CompressionCache` field
  `completion_claims` + `append_session_completion_claim`/
  `get_session_completion_claims`.
- `vendor/claude-code-proxy/llm/transformers/grounding_validator.py` — structured
  block extraction, regex fallback, cross-check against conversation-wide edited
  paths, system-note injection.
- `vendor/claude-code-proxy/tests/test_pipeline.py` — updated expected-fields set.
- `vendor/claude-code-proxy/tests/test_grounding_validator.py` — new test cases.

## Verification

- Full proxy suite run after implementation, confirming no regressions against the
  `1191 passed` baseline.
- New unit tests: strong-signal match/mismatch, weak-signal match/mismatch, no
  double-counting when both signals could apply, suffix-path matching.
