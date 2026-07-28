# ADR-0033: Detect Ungrounded Generality Claims

**Status:** Accepted
**Date:** 2026-07-27
**Supersedes:** —
**Superseded by:** —

---

## Context

Twice this session we evaluated how Kimi K2 (routed through this proxy) reasons about
"is this component safe/correct across all its usages?" — both times it concluded with
a confident generalization ("en la práctica casi todos los callers respetan el
contrato") without ever running a Grep/Glob sweep over the actual call sites. Verifying
it independently (reading real call sites in `school-system`) surfaced a genuine,
concrete bug the ungrounded generalization let slip through: `app/admin/student-groups/page.tsx`
captures `searchTerm` inside `fetchFn` but never lists it in `dependencies`, leaving the
server-side search branch dead code.

`GroundingValidatorTransformer` (ADR-0031) already cross-checks **completion claims**
("ya arreglé X") against real Edit/Write/MultiEdit evidence. This ADR applies the same
grounding philosophy to a different claim shape: **generality/universality claims**
("all callers", "always happens", "every consumer does X") made without evidence that a
codebase-wide search (Grep/Glob, or `grep`/`rg` via Bash) actually happened.

Prior investigation (via targeted exploration, not re-derived here):
- No reusable "was Grep/Glob used in this conversation?" tracker exists. The closest is
  a Read+Grep+Glob-lumped, current-response-scoped scanner in `intent_classifier.py`,
  and two current-turn-only checks in `utils/quality.py` (H16,
  `_detect_speculative_generation`). None fit as-is — a new function is needed,
  mirroring `_extract_all_edited_paths` (ADR-0031) but for search tools instead of
  write tools.
- `TransformContext` (`llm/pipeline.py`) had no related field before this change.
- Test construction in this file uses a `MockRequest(content, messages)` class (not
  `SimpleNamespace`) — see `tests/test_grounding_validator.py`.

## Decision

### 1. Detection — regex, sentence-level co-occurrence

Mirrors `_extract_completion_claims`'s design: a claim only counts when a generality
marker AND a usage-scope noun appear in the SAME sentence (reusing the existing
`_SENTENCE_SPLIT_RE`), to avoid false positives like "arreglé todos los tests" (already
ADR-0031's territory, not this one).

### 2. Evidence — new conversation-wide scan

A new function checks the WHOLE conversation for a `Grep`/`Glob` `tool_use`, OR a
`Bash` `tool_use` whose `command` matches `grep`/`rg`/`ag` — the Bash-grep branch is
necessary because agents (including this one, repeatedly, this session) frequently
search via shell rather than the dedicated tool; without it, false positives would be
constant.

### 3. Integration — Step 9 in `GroundingValidatorTransformer.transform()`

One evidence check per response (not per-claim, since search evidence isn't tied to any
single claim). On a generality claim with zero search evidence: append to
`ctx.unverified_generality_claims`, append to `ctx.grounding_issues`, and inject a
deliberately hedged `ensure_system_note` — hedged because this signal only proves
*absence* of any search, never sufficiency of one ("if you already searched, this is a
false positive; if you haven't, verify before asserting broadly").

### 4. Persistence — mirrors `llm/session/completion_claims.py` (ADR-0032)

New `llm/session/generality_claims.py` (`append_session_generality_claim`/
`get_session_generality_claims`), new `generality_claims: list[dict]` field on
`_CompressionCache` (`llm/session/store.py`), capped at 50, included in disk
save/load, re-exported from the `llm/compressor.py` facade.

Every detected claim is persisted — verified AND unverified — with a `verified: bool`
flag, matching how `completion_claims` already persists both outcomes. This gives a
full audit trail for future threshold/regex calibration, not just the flagged subset.

### 5. Session-level summary (added after user review of the initial plan)

The proxy has no real "task ended" signal (it's not a Claude Code hook watching one
session's transcript) — so the practical equivalent already exists in this codebase:
adaptive enforcement keyed on session history, in
`llm/transformers/intent_enforcement.py`'s existing "Adaptive quality enforcement"
block (reads `get_session_quality_history`, escalates a `[SESSION-QUALITY]` note past a
threshold). A sibling arm is added there: reads `get_session_generality_claims`,
counts unverified entries, and injects a `[SESSION-GENERALITY]` note once 2+ have
accumulated in the session — same shape, same threshold convention as the existing
`stub_count >= 2` check. Note this transformer calls `ensure_system_note(request, ...)`
(not `ctx`), matching its own existing convention rather than `grounding_validator.py`'s.

## Explicitly Out of Scope

- **Semantic search-coverage verification** ("did the search actually cover what the
  claim asserts?") — not reliably checkable without executing/understanding the Grep
  query itself. This ADR only proves presence/absence of *any* search, never adequacy.
- **LLM classifier instead of regex for claim detection** — evaluated and deferred
  (explicit user decision). The repo already has the exact pattern for this:
  `classify_intent` (`router/llm_router.py:310`) — a cheap LLM call with
  `response_format: json_object`, a 3s timeout, and a circuit breaker, falling back to
  regex on failure or when `CLASSIFIER_MODEL` is unset. Not mirrored now because of the
  added per-response latency/cost, and because this deployment currently runs with
  `CLASSIFIER_MODEL` unset (regex fallback already active for intent classification,
  per the startup log) — a similarly-gated classifier wouldn't fire in practice without
  further configuration anyway. If the generality regex proves too noisy in practice,
  this is the natural upgrade path — same pattern, no redesign needed.
- Updating the 10 external call sites that import from `llm.compressor` — unaffected.

## Consequences

- A model asserting universal caller/usage behavior with zero search evidence in the
  conversation gets an immediate, same-turn, deliberately-hedged nudge — a
  self-correction opportunity, not a hard block.
- Repeated unverified generality claims within a session escalate into a stronger,
  session-level note via the existing adaptive-enforcement mechanism, no new
  infrastructure required.
- Residual, accepted limitation: the evidence check is conversation-wide and binary —
  ANY Grep/Glob/Bash-grep anywhere in the conversation, regardless of what it searched
  for, counts as "verified." This can under-flag (a search for something unrelated
  still counts) but never over-flags (it won't punish a model that genuinely searched).
  Consistent with the "weak signal, hedge accordingly" framing throughout this ADR.

## Files Changed

- `vendor/claude-code-proxy/llm/pipeline.py` — new `TransformContext` field
  `unverified_generality_claims`.
- `vendor/claude-code-proxy/llm/session/store.py` — new `_CompressionCache` field
  `generality_claims` + disk save/load.
- `vendor/claude-code-proxy/llm/session/generality_claims.py` — new module,
  `append_session_generality_claim`/`get_session_generality_claims`.
- `vendor/claude-code-proxy/llm/compressor.py` — re-export of the two new functions.
- `vendor/claude-code-proxy/llm/transformers/grounding_validator.py` — new regexes,
  `_extract_generality_claims`, `_has_search_tool_evidence`, Step 9.
- `vendor/claude-code-proxy/llm/transformers/intent_enforcement.py` — sibling arm to
  the existing adaptive-quality block, `[SESSION-GENERALITY]` escalation.
- `vendor/claude-code-proxy/tests/test_pipeline.py` — updated expected-fields set.
- `vendor/claude-code-proxy/tests/test_grounding_validator.py` — new test cases.
- `vendor/claude-code-proxy/tests/test_intent_enforcement.py` — new test class.

## Verification

- Full proxy suite run after implementation, confirming no regressions against the
  `1197 passed` baseline.
- New unit tests: claim without evidence (flagged, persisted `verified=False`), claim
  with native Grep evidence (not flagged, persisted `verified=True`), claim with
  Bash-grep evidence (not flagged), text without a generality marker (no effect),
  session-level escalation at 2+ unverified claims (mirroring the existing
  `TestAdaptiveQualityEnforcement` test class).
