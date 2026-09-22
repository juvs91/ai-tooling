# ADR-0057: Replay original stream chunks when refinement returns an empty response

- **Date**: 2026-09-21
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

The proxy's streaming quality pipeline (`stream_response_pipeline` in `llm/transformers/quality_refinement.py`) buffers an upstream stream, scores it, and — when the score is below threshold — issues a non-streaming re-request with feedback, then delivers the refined response to the client.

Reproducible failure observed with Kimi Code CLI (model `kimi-for-coding`) on a 4-turn conversation: the final "summarize what we agreed" turn was classified as `SYNTHESIZING`, the first pass scored 0.40, refinement was triggered, and the refined non-stream response ended up **empty** (`stream-quality score=0.00 issues=['empty_response']`). The client received a `200 OK` with a single empty text block and zeroed usage — the CLI printed nothing and ended the turn "completed". Reproduced 3/3 times, including with a rephrased prompt.

Root cause chain (grounded in container logs and the client session `wire.jsonl`):

1. Intent classifier LLM labels the summary request `SYNTHESIZING` (regex disagreed: `CHAT`).
2. Proxy injects its synthesis prompt; first stream scores 0.40 (`lacks_specificity`, `unverified_bug_claims`) → `REFINE`.
3. Non-stream re-request: the response pipeline's universal tool extraction pulled 2 spurious "tools" from the model output, and the final response delivered to the client contained no text.
4. The refinement block only falls back to the original chunks on **exceptions** (`except Exception → replay original`). An empty-but-successful refined response passes through and is delivered as-is.

So the safety invariant "the client must never receive less content than the original stream had" was violated: a quality gate meant to *improve* responses became a path that silently delivers nothing.

## Decision

1. **Fail-safe on empty refinement in `stream_response_pipeline`.** After converting the refined non-stream response, extract its text. If the refined response has no text content **and** the original buffered stream had non-empty text, log a warning and replay the original chunks instead of delivering the empty refined response.

2. **Only guard the text-empty case.** If the original stream was already empty, keep current behavior (nothing worse is delivered). If the refined response has text, deliver it as today — quality comparisons beyond emptiness are out of scope.

3. **No provider/model-specific logic.** The check uses only `extract_response_text()` on the converted Anthropic response; no model names, intents, or provider quirks are inspected. The guard applies to every intent equally.

4. **Leave the non-streaming refinement path (`analysis_quality_nonstream`) unchanged.** It gates refinement on grounding score and has no observed empty-response failure; fixing it speculatively would widen the blast radius without evidence.

## Consequences

**Positive:**
- The quality pipeline can no longer degrade a response to nothing: worst case, the client receives the original (0.40-scored) content instead of an empty message.
- Fixes the observed Kimi Code CLI multi-turn failure deterministically — the turn that previously returned empty now returns the original analysis.

**Negative / Trade-offs:**
- A response the scorer considers "bad" (0.40) may now reach the client when the refinement produces nothing usable. This is intentional: scored-bad-content beats no-content, and the score itself remains logged for observability (`ctx.quality_score`, `/api/logs`).
- The first stream is still buffered before delivery (existing behavior, unchanged latency profile); this decision adds only a text-extraction check on the refinement path.

## Files Modified

| File | Change |
|------|--------|
| `vendor/claude-code-proxy/llm/transformers/quality_refinement.py` | In `stream_response_pipeline` refinement block: after `convert_litellm_to_anthropic`, extract refined text; if empty while original text was non-empty, log and replay original chunks. |
| `vendor/claude-code-proxy/tests/test_quality.py` | New async tests: empty refined response replays original chunks; non-empty refined response is delivered (no regression). |
| `docs/adr/ADR-0057-refinement-empty-response-replay-original.md` | This ADR. |
