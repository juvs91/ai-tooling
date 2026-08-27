# ADR-0054: Passthrough route override credentials for per-model API keys

- **Date**: 2026-08-22
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

The proxy has `RouteOverride` env vars (`SMALL_*`, `BUILDING_*`) that allow a model route to use a different provider, base URL, and API key. They were originally designed for cross-provider mixed configurations where `BIG_MODEL` lives on one provider and `SMALL_MODEL`/`BUILDING_MODEL` live on another.

For the Kimi provider profile (`PREFERRED_PROVIDER=anthropic`):

- All three internal model types (`big`, `small`, `building`) map to the same underlying model name (`kimi-for-coding`).
- Requests go through the Anthropic-compatible **passthrough** path, not LiteLLM.
- `kimi.env` currently uses a single `ANTHROPIC_API_KEY` for all model types.

The user wanted the ability to configure a **different API key per model type** while keeping all requests on the Kimi passthrough path.

### Existing limitations discovered during analysis

1. `ModelRouterTransformer` only applied `small_route`/`building_route` when the model name differed from `big_model` (`model_router.py:104` and `:112`).
2. The passthrough path in `proxy.py` created `PassthroughClient` using only `cfg.credentials.anthropic_base_url` and `cfg.credentials.anthropic_api_key`, ignoring `ctx.route_override`.
3. `_is_passthrough_compatible` required the primary `ANTHROPIC_API_KEY` to be set, so a request with only a route override key would fall back to LiteLLM instead of using passthrough.

## Decision

1. **Remove the model-name gate for route overrides.** `small_route` and `building_route` are now applied whenever they are configured, even if `SMALL_MODEL == BIG_MODEL == BUILDING_MODEL`.

2. **Make the passthrough path honor `ctx.route_override`.** When the override provider is `anthropic`, passthrough uses the route's `api_key` and `base_url`. This gives Kimi per-model API keys without changing the underlying model name.

3. **Keep `big_model` on primary credentials.** No `BIG_PROVIDER`/`BIG_API_KEY` env vars are introduced. `big_model` continues to use the primary `ANTHROPIC_API_KEY`.

4. **Document the override precedence.** A new `docs/ROUTING.md` file explains primary credentials, per-route overrides, analysis overrides, fallback chain, and passthrough behavior.

## Consequences

**Positive:**
- Kimi can now use separate API keys for EXPLORE (`SMALL_*`) and EXECUTE-with-tools (`BUILDING_*`) while keeping all requests on the passthrough path.
- LiteLLM-only profiles also benefit: same-model-name overrides work there too.
- The change is backward-compatible: env files without `SMALL_*`/`BUILDING_*` behave exactly as before.

**Negative / Trade-offs:**
- Extends `RouteOverride` semantics from "different provider" to "same provider, different key". Docstrings and `docs/ROUTING.md` must be kept current to avoid confusion.
- Passthrough path now has an extra credential-resolution branch in `_get_passthrough_credentials` and `_is_passthrough_compatible`.

## Files Modified

| File | Change |
|------|--------|
| `llm/transformers/model_router.py` | Apply `small_route`/`building_route` whenever configured, regardless of model-name equality. |
| `proxy/proxy.py` | `_get_passthrough_credentials`, `_is_passthrough_compatible`, and `PassthroughClient` creation use route override credentials when provider == `anthropic`. |
| `tests/test_model_router.py` | Same-model-name route override tests. |
| `tests/test_passthrough_routing.py` | New tests for passthrough credential resolution. |
| `docs/ROUTING.md` | New routing/credential override documentation. |
| `README.md` | Link to `docs/ROUTING.md`. |
| `cloud-provider-env/kimi.env` | Commented `SMALL_*`/`BUILDING_*` API key blocks with usage note. |
| `docs/adr/ADR-0054-passthrough-route-override-credentials.md` | This ADR. |
