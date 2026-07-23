# ADR-0030: Deterministic Session ID Fallback for Plan-Mode Persistence

**Status:** Accepted
**Date:** 2026-07-22
**Supersedes:** —
**Superseded by:** —

---

## Context

`IntentClassifierTransformer` (`llm/transformers/intent_classifier.py`) persists
`plan_mode_active` across turns via a session cache (`get_session_plan_mode`/
`set_session_plan_mode` in `llm/compressor.py`), keyed by `ctx.session_id`. That ID
is populated exclusively from the `X-Session-ID` request header
(`server.py:251`: `raw_request.headers.get("X-Session-ID") or None`). Every read/write
of the plan-mode cache in `intent_classifier.py` was guarded by `if ctx.session_id:`
with **no fallback** — when the header is absent, the guard is simply skipped and
`plan_mode_active` is recomputed from scratch every turn using only the current
turn's own signals (message-history scan, live system-prompt text, this-turn's
classifier intent). Nothing persists across turns.

Live incident, confirmed via the proxy's `/api/logs` endpoint for a real Kimi K2
session in `school-system` run through Claude Code's VS Code extension
(2026-07-22, session transcript `14a7fb6e-c430-4d29-9652-dcbb6882cd9d.jsonl`):
`session_id` was empty (`""`) for all 88 logged requests in that session. Intent
oscillated READ → SYNTHESIZING → PLAN → BUILD turn to turn (real LLM classifier,
`openai/deepseek-chat`, not the regex fallback). Every time the classifier happened
to return `PLAN` for one turn, Signal 2 set `plan_mode_active=True` for that turn
only — the very next turn, with no cache to fall back to, it reverted to `False`.

`DeferredToolsTransformer` (`llm/transformers/deferred_tools.py`) strips
`EnterPlanMode`/`ExitPlanMode` from `request.tools` whenever
`not ctx.plan_mode_active and ctx.intent not in ("PLAN",)` (ADR-0012's "final
gate"). With `plan_mode_active` flickering true for isolated single turns and false
otherwise, the model was left with a narrow, unpredictable window — never stable
enough to reliably call `EnterPlanMode`. Across the whole session it never did. The
model instead improvised an equivalent-looking workflow via `Agent`-tool
sub-delegation (Explore + Plan subagent types) and wrote a plan-shaped markdown
directly via `Write`/`Edit` — never engaging Claude Code's native plan-approval UI.

`DeferredToolsTransformer` already solves exactly this problem for its own cache
(`_compute_deferred_session_id`, `deferred_tools.py:50-64`): when `ctx.session_id`
is falsy, it computes a deterministic ID by hashing the first 20 messages of the
conversation (`sha256` → `uuid5`), stable for the life of that conversation
regardless of whether the client ever sends `X-Session-ID`.
`IntentClassifierTransformer` never adopted the same fallback for its own
session-cache calls.

## Decision

Reuse `DeferredToolsTransformer`'s existing `_compute_deferred_session_id(messages)`
helper inside `IntentClassifierTransformer`. Compute
`effective_sid = ctx.session_id or _compute_deferred_session_id(messages)` once at
the top of `transform()`, and use `effective_sid` (not `ctx.session_id`) for every
plan-mode cache read/write in that method: the LLM-classifier context injection
(`get_session_plan_mode`), Signal 3, Signal 4's source lookup, source-tracking
writes, and the final per-turn persist. `ctx.session_id` itself is left untouched —
other transformers/code paths that read it directly are unaffected.

Both transformers now derive the *same* fallback ID for the same conversation
(same hashing of the same message prefix), so plan-mode state and deferred-tools
state stay consistent with each other even when the real header is missing.

Alternatives considered:
- **Do nothing, treat this as a Claude Code / VS Code extension bug** (it "should"
  send `X-Session-ID`). Rejected: the proxy cannot control what a specific Claude
  Code build/mode sends, and the fix is a genuinely more robust design regardless —
  session identity derived from the conversation itself is a reasonable substitute
  when no explicit ID is provided, and it is already the accepted pattern one
  transformer over.
- **Add a new proxy-level session ID generator/header-injection shim.** Rejected:
  more moving parts than reusing an existing, already-tested helper in the same
  codebase; `_compute_deferred_session_id` already covers this need.

## Consequences

- Plan-mode activation/deactivation now persists across turns for any client that
  omits `X-Session-ID`, not just ones that send it.
- No behavior change for clients that DO send `X-Session-ID` (real header always
  wins via the `or` fallback).
- Residual: the conversation-prefix hash is stable only for the *life of that
  specific prefix* — if the client ever resends a truncated/compacted history whose
  first 20 messages differ from the original, the derived ID changes and cached
  state under the old ID becomes orphaned (same limitation `DeferredToolsTransformer`
  already accepted for its own cache).

## Files Changed

- `vendor/claude-code-proxy/llm/transformers/intent_classifier.py` — import
  `_compute_deferred_session_id` from `deferred_tools.py`; compute `effective_sid`
  once in `transform()`; replace all `ctx.session_id` uses in that method with it.

## Verification

- Full proxy suite: `1191 passed, 0 failed` (no regressions).
- Manual repro: two-turn conversation, `session_id=None` both turns, same message
  prefix. Turn 1 classifies `PLAN` (Signal 2 activates `plan_mode_active=True`).
  Turn 2 classifies `CHAT` (a continuation) — before this fix `plan_mode_active`
  would revert to `False`; confirmed it now stays `True`.
