# ADR-0005: Configurable Anthropic Passthrough Endpoint Path

**Status:** Accepted  
**Deciders:** jeguzman, Claude Sonnet 4.6  
**Date:** 2026-05-26  
**Technical Story:** Kimi K2 exposes an Anthropic-compatible API at `/coding/v1/messages` — the hardcoded `/v1/messages` path in `PassthroughClient` blocks its integration.

---

## Context and Problem Statement

`PassthroughClient` hardcodes the Anthropic messages path to `/v1/messages`. Providers that implement the Anthropic wire format at a non-standard path (e.g., Kimi K2's `/coding/v1/messages`) cannot be used via passthrough mode. How should the proxy support arbitrary Anthropic-compatible endpoints without encoding provider-specific paths in source code?

---

## Decision Drivers

- Kimi K2 (Moonshot AI) exposes a native Anthropic-format API at `https://api.kimi.com/coding/v1/messages`, enabling tool use and extended thinking with better fidelity than the OpenAI-compat path.
- The proxy must remain provider-agnostic — no provider-specific branching in `passthrough.py`.
- The env-var config pattern already in use (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`) is the established extension point.

---

## Considered Options

1. **Provider-specific branching** — detect Kimi in `passthrough.py` and switch path conditionally
2. **Configurable endpoint path via env var** (`ANTHROPIC_ENDPOINT_PATH`) — chosen
3. **Full URL override** (`ANTHROPIC_MESSAGES_URL`) — replaces both base URL and path

---

## Decision Outcome

**Chosen option: "Configurable endpoint path via env var"**, because it follows the existing config extension pattern, requires no provider-detection logic in the client, and defaults to `/v1/messages` so existing deployments are unaffected.

### Positive Consequences

- Kimi K2 native Anthropic-format integration works with `ANTHROPIC_ENDPOINT_PATH=/coding/v1/messages`.
- `PassthroughClient._url()` becomes a pure builder with no branching.
- Any future provider with a non-standard path is supported with a single env var, no code change.

### Negative Consequences

- Operators must know to set `ANTHROPIC_ENDPOINT_PATH` when using non-standard providers — not discoverable without docs.
- Full URL override (option 3) would be more explicit; if base URL and path diverge frequently, a single `ANTHROPIC_MESSAGES_URL` may be cleaner. Deferred.

---

## Pros and Cons of the Options

### Provider-specific branching

- Good, because no new config surface
- Bad, because it violates provider-agnosticism and grows with every new provider

### Configurable endpoint path via env var ✓

- Good, because consistent with existing config pattern
- Good, because zero impact on existing deployments (default = `/v1/messages`)
- Bad, because adds one env var per non-standard provider

### Full URL override

- Good, because maximum flexibility (base URL + path in one variable)
- Bad, because duplicates `ANTHROPIC_BASE_URL`, creating two sources of truth for the same provider URL

---

## Links

- Supersedes nothing — new capability
- Related: [ADR-0004](ADR-0004-long-session-reliability-multi-provider-proxy.md) (multi-provider passthrough architecture)
- Code: `vendor/claude-code-proxy/config.py` (`ProviderCredentials.anthropic_endpoint_path`)
- Code: `vendor/claude-code-proxy/llm/passthrough.py` (`PassthroughClient.__init__`, `_url()`)
- Code: `vendor/claude-code-proxy/proxy/proxy.py` (passes `endpoint_path` to `PassthroughClient`)
- Config: `profile-envs/cloud.kimi-coding.env` (`ANTHROPIC_ENDPOINT_PATH=/coding/v1/messages`)
