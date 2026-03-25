# router/model_mapper.py
from __future__ import annotations

KNOWN_PREFIXES = ("openai/", "anthropic/", "gemini/")

def has_provider_prefix(model: str) -> bool:
    return any(model.startswith(p) for p in KNOWN_PREFIXES)

def strip_provider_prefix(model: str) -> str:
    for p in KNOWN_PREFIXES:
        if model.startswith(p):
            return model[len(p):]
    return model


def build_model_name(provider: str, model: str) -> str:
    """Return 'provider/bare_model', stripping any embedded prefix from model.

    provider: PREFERRED_PROVIDER name ('anthropic', 'openai', 'google') or a
              route provider ('gemini'), or an already-prefixed string ('anthropic/').
              'google' is normalised to 'gemini' for LiteLLM compatibility.
    model: bare or prefixed model name — any existing prefix is stripped first.
    """
    p = provider.rstrip("/").lower()
    normalized = "gemini" if p == "google" else p
    return f"{normalized}/{strip_provider_prefix(model)}"


def map_claude_alias_to_target(
    model: str,
    *,
    preferred_provider: str,
    big_model: str,
    small_model: str,
) -> str:
    """
    Map Claude Code aliases (claude-sonnet-*, claude-haiku-*, claude-opus-*) to
    a real model with the correct LiteLLM prefix:
      - openai/<big|small>     (Z.AI OpenAI-compat, Groq, DeepSeek, etc.)
      - anthropic/<big|small>  (Z.AI Anthropic-compat, native Anthropic)
      - gemini/<big|small>     (Google AI / Vertex)

    Note: model names are always bare (no embedded provider prefix).
    Cross-provider routing is handled by RouteOverride in ModelRouterTransformer.
    """
    if not model:
        return build_model_name(preferred_provider, small_model)

    # Already has a provider prefix — keep as-is
    if has_provider_prefix(model):
        return model

    clean = strip_provider_prefix(model)
    low = clean.lower()

    # Claude aliases → big/small buckets
    if "haiku" in low:
        return build_model_name(preferred_provider, small_model)
    if "sonnet" in low:
        return build_model_name(preferred_provider, big_model)
    if "opus" in low:
        return build_model_name(preferred_provider, big_model)

    # Not a Claude alias, no prefix: route to preferred provider as-is
    # clean is already bare so strip_provider_prefix inside build_model_name is a no-op
    return build_model_name(preferred_provider, clean)
