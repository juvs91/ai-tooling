# ADR-0056: Normalize passthrough SSE event delimiters

- **Date**: 2026-08-29
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

The proxy's Anthropic-compatible passthrough path relays upstream SSE streams to clients such as Claude Code and Kimi Code CLI. The SSE specification requires events to be separated by a blank line (`\n\n`). Some upstream providers—notably Kimi's `/coding/v1` endpoint—emit consecutive `event:`/`data:` lines without blank-line delimiters.

When the proxy relayed those lines unchanged, the resulting stream looked like:

```
event:message_start\ndata:{...}\nevent:content_block_start\ndata:{...}
```

Standard SSE parsers in Kimi Code CLI and other clients treat the entire sequence as a single malformed event, find no usable content, and report an empty response or hang waiting for properly-delimited events.

This is a wire-format issue independent of model names, providers, or content semantics: any upstream that omits SSE delimiters can trigger it.

## Decision

1. **Normalize SSE event delimiters in the passthrough streaming path.** Every event yielded to the client must end with `\n\n`.

2. **Handle both strict and relaxed upstream formats.** If the upstream already emits blank-line delimiters, preserve them without creating double blank lines. If the upstream omits blank lines, use the start of a new `event:` line as the boundary of the previous event.

3. **Keep the normalization provider/model-agnostic.** No checks for provider names, model names, or endpoint paths. The logic only inspects SSE line patterns (`event:`, `data:`, blank lines).

4. **Preserve existing transformations.** Model-name rewriting, thinking-signature padding normalization, reasoning-tag stripping, and metrics collection continue to run inside the same event pipeline.

5. **Implement the change inside `llm/passthrough.py`.** The passthrough streaming generator now accumulates lines into complete events before yielding them.

## Consequences

**Positive:**
- Anthropic-compatible clients with strict SSE parsers (including Kimi Code CLI) can now consume passthrough streams correctly.
- The fix is provider-agnostic and model-agnostic.
- Existing behavior for spec-compliant upstreams is unchanged; only malformed streams are repaired.

**Negative / Trade-offs:**
- Adds a small per-event buffering step in the passthrough streaming path. Events are tiny (a few hundred bytes each), so memory overhead is negligible.
- Consumers that previously relied on receiving raw upstream line chunks may now receive complete event chunks. All internal proxy consumers (`passthrough_xml_tool_extraction`, `stream_response_pipeline`, `tracked_stream`) parse SSE by lines and tolerate complete-event chunks.

## Files Modified

| File | Change |
|------|--------|
| `vendor/claude-code-proxy/llm/passthrough.py` | Refactor `stream_message()` to accumulate events and yield them with proper `\n\n` delimiters. Extract per-event transformation into `_transform_sse_event()`. |
| `vendor/claude-code-proxy/tests/test_passthrough_sse_normalization.py` | New tests for malformed and spec-compliant SSE streams, signature normalization inside events, and response-model rewriting. |
| `docs/adr/ADR-0056-normalize-passthrough-sse-delimiters.md` | This ADR. |
