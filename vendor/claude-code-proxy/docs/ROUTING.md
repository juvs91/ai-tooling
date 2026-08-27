# Proxy Routing & Credential Override Guide

This document describes how the proxy decides which backend provider and API key to use for each request.

## Model types

Claude Code sends model aliases such as `claude-sonnet-*` and `claude-haiku-*`. The proxy maps these to three internal model types:

| Model type | Used for | Default env |
|---|---|---|
| `big_model` | PLAN, ANALYZING/READ, EXECUTE with no tools | `BIG_MODEL` |
| `small_model` | EXPLORE / lightweight chat | `SMALL_MODEL` |
| `building_model` | EXECUTE with tools (code changes) | `BUILDING_MODEL` |

All three can point to the same underlying model name (e.g., `kimi-for-coding`) and still use different API keys.

## Credential precedence

From highest to lowest priority:

1. **`FALLBACK_N_*`** — used only when the primary/fallback chain reaches that fallback provider.
2. **`ANALYSIS_MODEL` + `ANALYSIS_API_KEY` + `ANALYSIS_BASE_URL`** — used only during the `SYNTHESIZING` analysis phase.
3. **Per-route overrides** — `SMALL_*` and `BUILDING_*` env vars.
4. **Primary provider credentials** — `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` based on the model prefix.

## Per-route overrides

These env vars exist today:

```bash
# SMALL route (EXPLORE phase)
SMALL_PROVIDER=anthropic        # litellm provider prefix
SMALL_API_KEY=sk-...
SMALL_BASE_URL=https://api.kimi.com/coding/v1
SMALL_CONTEXT_WINDOW=262144

# BUILDING route (EXECUTE phase with tools)
BUILDING_PROVIDER=anthropic
BUILDING_API_KEY=sk-...
BUILDING_BASE_URL=https://api.kimi.com/coding/v1
BUILDING_CONTEXT_WINDOW=262144
```

When a route is configured, the proxy:

- Builds the request model as `{provider}/{model}` (e.g., `anthropic/kimi-for-coding`).
- Stores the route in `ctx.route_override`.
- Injects the route's `api_key` and `base_url` instead of the primary credentials.

A route is triggered whenever its env vars are set, **not** only when the model name differs from `big_model`. This allows the same model name (e.g., `kimi-for-coding`) to use different API keys per phase.

There is **no `BIG_*` route**. `big_model` always uses the primary provider credentials.

## Passthrough behavior

Anthropic-compatible endpoints (models prefixed with `anthropic/`, e.g., Kimi) use the passthrough path.

- Passthrough uses `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` by default.
- If `SMALL_*` or `BUILDING_*` routes use `SMALL_PROVIDER=anthropic` / `BUILDING_PROVIDER=anthropic`, passthrough uses the route's `api_key` and `base_url` for that request.
- The semaphore and provider concurrency limits are keyed by the effective base URL.

## Example: Kimi with per-model API keys

```bash
PREFERRED_PROVIDER=anthropic
ANTHROPIC_BASE_URL=https://api.kimi.com/coding/v1
ANTHROPIC_API_KEY=<primary-key>          # used for big_model
ANTHROPIC_ENDPOINT_PATH=/messages

# All models can be the same name
BIG_MODEL=kimi-for-coding
SMALL_MODEL=kimi-for-coding
BUILDING_MODEL=kimi-for-coding

# SMALL and BUILDING use their own keys
SMALL_PROVIDER=anthropic
SMALL_API_KEY=<small-key>
SMALL_BASE_URL=https://api.kimi.com/coding/v1

BUILDING_PROVIDER=anthropic
BUILDING_API_KEY=<building-key>
BUILDING_BASE_URL=https://api.kimi.com/coding/v1
```

## Fallback chain

`FALLBACK_1_*` through `FALLBACK_9_*` define a chain of backup providers. They are used only when the primary call fails after retries. See `config.py:_load_fallback_providers` for the exact env var names.

## LiteLLM vs passthrough

- `anthropic/...` models go through passthrough when `ANTHROPIC_BASE_URL` is set and passthrough is enabled.
- `openai/...`, `gemini/...`, and other prefixes go through LiteLLM.
- Route overrides work for both paths: LiteLLM gets credentials via `CredentialTransformer`; passthrough gets credentials via `_get_passthrough_credentials` in `proxy.py`.
