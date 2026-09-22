# ADR-0055: Normalize thinking-block signature padding

- **Date**: 2026-08-28
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

The proxy can return `thinking` content blocks in both passthrough and routed responses. Per the Anthropic Messages API, each thinking block carries a `signature` field used to verify the block in multi-turn conversations.

Some upstream providers return signatures whose base64 length is not a multiple of 4 (e.g., 12,946 characters → 12,946 % 4 = 2). Adding the required `==` padding makes the string valid base64 and decodes to the expected number of bytes (9,709). Without padding, strict base64 decoders reject the signature.

Clients that validate or re-encode the signature may crash or reject the response when padding is missing. The issue is not provider-specific: any endpoint that emits under-padded base64 signatures can trigger it.

## Decision

1. **Normalize base64 padding for thinking signatures in responses**, both non-streaming and streaming.

2. **Keep the logic provider/model-agnostic.** No checks for model names, provider prefixes, or quirks. The helper only inspects the signature string itself.

3. **Only touch signatures that look like base64.** If the string contains characters outside the base64 alphabet, leave it unchanged so we do not corrupt genuinely opaque data.

4. **Make the helper idempotent.** Calling it on an already-valid signature is a no-op.

5. **Implement the normalization as a reusable transformer** (`ThinkingSignatureNormalizer` in `llm/transformers/thinking_signature_normalizer.py`) and register it in the agnostic response pipeline. The same pure helper is reused directly in the passthrough streaming path, because streaming SSE lines do not pass through the object-based response pipeline.

## Consequences

**Positive:**
- Anthropic-compatible clients that validate thinking signatures no longer crash on under-padded signatures.
- The change is provider-agnostic and model-agnostic.
- Minimal code footprint and no change to the proxy's request path.

**Negative / Trade-offs:**
- Adds a small per-response content walk. This is negligible compared to the network round-trip.
- If a future provider uses a non-base64 opaque signature of arbitrary length, the normalization is skipped and behavior is unchanged.

## Files Modified

| File | Change |
|------|--------|
| `vendor/claude-code-proxy/llm/transformers/thinking_signature_normalizer.py` | New module: pure `normalize_thinking_signature()` helper + `ThinkingSignatureNormalizer` transformer. |
| `vendor/claude-code-proxy/proxy/proxy.py` | Register `ThinkingSignatureNormalizer` in the agnostic response pipeline. |
| `vendor/claude-code-proxy/llm/passthrough.py` | Use `normalize_thinking_signature()` in non-streaming and streaming paths. |
| `vendor/claude-code-proxy/tests/test_thinking_signature_padding.py` | Tests for helper and transformer. |
| `docs/adr/ADR-0055-normalize-thinking-signature-padding.md` | This ADR. |
