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

def _provider_prefix(preferred_provider: str) -> str:
    """Map preferred_provider name to LiteLLM prefix: openai/ | anthropic/ | gemini/"""
    p = (preferred_provider or "openai").lower()
    if p == "google":
        return "gemini/"
    if p == "anthropic":
        return "anthropic/"
    return "openai/"

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
    """
    if not model:
        return _provider_prefix(preferred_provider) + small_model

    # Already has a provider prefix — keep as-is
    if has_provider_prefix(model):
        return model

    clean = strip_provider_prefix(model)
    low = clean.lower()
    pref = _provider_prefix(preferred_provider)

    # Claude aliases → big/small buckets
    if "haiku" in low:
        return pref + small_model
    if "sonnet" in low:
        return pref + big_model
    if "opus" in low:
        return pref + big_model

    # Not a Claude alias, no prefix: route to preferred provider as-is
    return pref + clean
