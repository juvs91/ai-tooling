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
    """
    preferred_provider esperado: "openai" | "google" | "anthropic"
    OJO: para Google usamos "gemini/" porque así lo maneja LiteLLM en tu código.
    """
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
    Convierte aliases estilo Claude Code ("claude-sonnet-*", "claude-haiku-*")
    a un target real con prefijo correcto:
      - openai/<big|small>  (incluye Groq porque es openai-compatible)
      - gemini/<big|small>  (google)
      - anthropic/<...>     (si de verdad quieres Anthropic directo)
    """
    if not model:
        return _provider_prefix(preferred_provider) + small_model

    # si ya viene con prefijo, respétalo
    if has_provider_prefix(model):
        return model

    clean = strip_provider_prefix(model)
    low = clean.lower()
    pref = _provider_prefix(preferred_provider)

    # Alias Claude -> tus buckets
    if "haiku" in low:
        return pref + small_model
    if "sonnet" in low:
        return pref + big_model
    if "opus" in low:
        return pref + big_model

    # Si NO es alias de claude, pero viene sin prefijo: lo mandamos al provider preferido tal cual
    return pref + clean
