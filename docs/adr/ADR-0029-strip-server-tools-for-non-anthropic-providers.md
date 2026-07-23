# ADR-0029: Strip Anthropic Server-Side Tools for Non-Anthropic Providers

**Status:** Accepted
**Date:** 2026-07-21
**Supersedes:** —
**Superseded by:** —

---

## Context

Claude Code sends Anthropic's server-side native tool definitions — e.g.
`{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}` — whenever it
believes the endpoint supports them. These tools have no `input_schema` because
Anthropic's own backend resolves them server-side; the client/proxy never executes
them directly.

The proxy's `Tool` Pydantic model (`llm/schemas.py:101-104`) required `input_schema`
with no default, so FastAPI rejected any request containing a server-tool with a 422
at request ingestion — before any proxy/routing logic ran. This crashed the entire
conversation turn whenever Claude Code tried to use `WebSearch` (or any other native
server-tool) while routed through this proxy, observed live in a Kimi K2 session
(`ai-notes` session transcript, 2026-07-21).

Investigation (3 passes, see plan file `wild-herding-tide.md` history for full
file:line citations) established:

1. **`Tool` model gap**: no `input_schema` default, no `type` field, and no
   `model_config` override — Pydantic's default `extra="ignore"` would silently drop
   `type`/`max_uses` even if `input_schema` were made optional.
2. **litellm/OpenAI conversion path** (`utils/schema_utils.py:164`,
   `llm/converters.py:373-384`): structurally incapable of representing a
   server-executed tool as `function`-calling — there is no schema translation that
   makes sense here, since the target model would receive a tool it can never
   fulfill (Anthropic resolves the search itself; Kimi K2 cannot).
3. **Passthrough path** (`proxy/proxy.py:338`, forwards to real Anthropic): DOES
   support server-tools natively, and is unaffected by the routing decision — the 422
   happened before passthrough vs. conversion was even decided.
4. **No capability negotiation exists** between Claude Code and this proxy (no
   `anthropic-beta` header handling, no capabilities endpoint) — Claude Code decides
   to include the server-tool definition unconditionally when talking to an
   Anthropic-shaped `/v1/messages` endpoint. There is no way to signal "this backend
   doesn't support server-tools" from the proxy side to change what Claude Code
   sends.

## Decision

1. **`llm/schemas.py`**: make `Tool.input_schema` optional (default `None`), add an
   optional `type: str | None` field, and add `model_config = ConfigDict(extra="allow")`
   so unmodeled server-tool fields (`max_uses`, `allowed_domains`, etc.) survive
   `model_dump()`/`.dict()` intact instead of being silently dropped. This fixes the
   passthrough path completely (real Anthropic gets the full tool definition).
2. **`utils/schema_utils.py`**: before converting the `tools` array to OpenAI/litellm
   format for a non-Anthropic provider, filter out any tool whose `type` matches a
   known Anthropic server-tool pattern (`web_search_*`, `web_fetch_*`, `bash_*`,
   `code_execution_*`, `text_editor_*`, or any `type` with an Anthropic-style date
   suffix). These tools are **silently excluded** from what the backend model sees —
   chosen over rejecting the request outright, since the 422 crash was the actual
   symptom being fixed, and promising a capability the backend cannot fulfill is
   worse than the model simply not seeing the option.
3. Passthrough (`proxy/proxy.py:338`) is unaffected by the filter — real Anthropic
   receives the full, now-correctly-modeled tool definition.

Known limitation, accepted: if Claude Code always also offers its own client-side
`WebSearch`/`WebFetch` tools (with a real `input_schema`, executed by Claude Code
itself, independent of backend model) alongside the Anthropic-native server-tool, the
model still gets equivalent search capability via that tool — this proxy has no way
to confirm this from the server side (Claude Code's tool-offering logic is closed-source),
so it is not something this fix can verify, only assume.

## Consequences

**Positive:**
- No more 422 crash when Claude Code includes `WebSearch`/`WebFetch`/other
  server-tools while routed to a non-Anthropic provider (Kimi K2, etc.)
- Passthrough to real Anthropic now correctly preserves `type`/`max_uses` and any
  other server-tool fields that were previously at risk of silent truncation
- No false promise of unsupported capability to non-Anthropic backends

**Trade-offs:**
- `WebSearch`/`WebFetch`/other Anthropic-native server-tools become silently
  unavailable to the model when routed to a non-Anthropic provider — no error is
  surfaced to the user or the model when this happens
- `model_config = ConfigDict(extra="allow")` on `Tool` is intentionally permissive —
  any future malformed/unexpected tool field will pass through rather than fail fast

## Files Changed
1. `vendor/claude-code-proxy/llm/schemas.py` (`Tool` model: optional `input_schema`, `type` field, `ConfigDict(extra="allow")`)
2. `vendor/claude-code-proxy/utils/schema_utils.py` (filter server-tools before OpenAI/litellm conversion)
