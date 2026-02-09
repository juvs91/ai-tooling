# app/proxy/proxy.py
from __future__ import annotations
from typing import Any, Optional, Tuple

import litellm
import os

from utils.utils import (
    approx_tokens_from_bytes,
    parse_allowlist,
    ensure_system_note,
    filter_tools_allowlist,
    normalize_tool_choice,
)
from utils.metrics import metrics
from router.llm_router import choose_local_model
from llm.converters import convert_anthropic_to_litellm
from router.model_mapper import map_claude_alias_to_target


# Base guardrail (always injected). Can be extended via GUARDRAILS_FILE env var.
_DEFAULT_GUARD = (
    "[proxy-guard] IMPORTANT: If you do not have tool access (filesystem/bash/etc.) "
    "or you were not given the file contents, do NOT guess or fabricate. "
    "Explain what you need (enable tools or paste the content) and proceed only with available info."
)

def _load_guard_system() -> str:
    """Load guardrails from file if GUARDRAILS_FILE is set, otherwise use default."""
    
    gf = os.environ.get("GUARDRAILS_FILE", "").strip()
    if gf and os.path.isfile(gf):
        try:
            with open(gf, "r") as f:
                extra = f.read().strip()
            if extra:
                return _DEFAULT_GUARD + "\n\n" + extra
        except Exception:
            pass
    return _DEFAULT_GUARD

BASE_GUARD_SYSTEM = _load_guard_system()

def is_ollama_base(base_url: Optional[str]) -> bool:
    return bool(base_url) and ("11434" in base_url)

def system_chars(system_field: Any) -> int:
    if system_field is None:
        return 0
    if isinstance(system_field, str):
        return len(system_field)
    if isinstance(system_field, list):
        total = 0
        for b in system_field:
            if hasattr(b, "text"):
                total += len(b.text or "")
            elif isinstance(b, dict):
                total += len(b.get("text", "") or "")
        return total
    return 0

def provider_cap_for_base_url(base_url: Optional[str]) -> int:
    if not base_url:
        return 0
    b = base_url.lower()

    # Groq: TPM bajo en on_demand (tu error mostró 6000)
    if "api.groq.com" in b or "groq.com" in b:
        return 5500  # margen vs 6000

    # Ollama local: prácticamente sin TPM cap (depende de tu HW)
    if "11434" in b:
        return 25000  # ajusta a gusto

    # Otros: sin cap por defecto
    return 0


def apply_policy_and_routing(
    *,
    request_obj: Any,
    raw_body: bytes,
    openai_base_url: Optional[str],
    tool_allowlist_raw: str,
    policy_note_in_system: bool,
    max_input_tokens: int,
    hard_block_oversize: bool,
    small_model: str,
    big_model: str,
    building_model: str,
    preferred_provider: str,
    intent: str = "CHAT",
) -> Tuple[int, list[str]]:
    # Guardar modelo original para logging/debug
    if not getattr(request_obj, "original_model", None):
        setattr(request_obj, "original_model", getattr(request_obj, "model", None))


    # 1) Guardrail system note (SIN system_content_cls)
    ensure_system_note(request_obj, BASE_GUARD_SYSTEM)

    approx_tokens = approx_tokens_from_bytes(raw_body)

    # ✅ Cap específico por proveedor (antes de llegar a LiteLLM)
    cap = provider_cap_for_base_url(openai_base_url)
    if cap and approx_tokens > cap:
        msg = (
            f"[proxy-policy] Provider cap exceeded: approx_tokens={approx_tokens} > cap={cap} "
            f"(base_url={openai_base_url}). Reduce contexto / usa otro provider."
        )
        if hard_block_oversize:
            raise ValueError(msg)


    # 2) Hard cap
    if max_input_tokens > 0 and approx_tokens > max_input_tokens:
        msg = (
            f"[proxy-policy] Oversize request: approx_tokens={approx_tokens} > "
            f"MAX_INPUT_TOKENS={max_input_tokens}. Reduce workspace/context."
        )
        if hard_block_oversize:
            raise ValueError(msg)

    # 3) Tools allowlist
    allow = parse_allowlist(tool_allowlist_raw)
    if not allow:
        # drop everything
        dropped = []
        for t in (getattr(request_obj, "tools", None) or []):
            name = getattr(t, "name", None) if not isinstance(t, dict) else t.get("name")
            if name:
                dropped.append(name)
        request_obj.tools = None
        request_obj.tool_choice = None
    else:
        request_obj.tools, dropped = filter_tools_allowlist(getattr(request_obj, "tools", None), allow)
        request_obj.tool_choice = normalize_tool_choice(
            getattr(request_obj, "tool_choice", None),
            getattr(request_obj, "tools", None),
        )

    if dropped and policy_note_in_system:
        ensure_system_note(
            request_obj,
            f"[proxy-policy] Tools not allowed and were removed: {', '.join(dropped)}. Allowed: {', '.join(sorted(allow))}."
        )

    # 4) Model mapping SIEMPRE (haiku/sonnet -> provider + big/small)
    request_obj.model = map_claude_alias_to_target(
        getattr(request_obj, "model", ""),
        preferred_provider=preferred_provider,
        big_model=big_model,
        small_model=small_model,
    )

    # 5) Intent-based model override
    if is_ollama_base(openai_base_url):
        # Ollama: full scoring with intent
        chosen = choose_local_model(
            messages=getattr(request_obj, "messages", []) or [],
            max_out=int(getattr(request_obj, "max_tokens", 0) or 0),
            approx_tokens=approx_tokens,
            system_chars=system_chars(getattr(request_obj, "system", None)),
            tools_count=len(getattr(request_obj, "tools", None) or []),
            small_model=small_model,
            big_model=big_model,
            building_model=building_model,
            intent=intent,
        )
        request_obj.model = f"openai/{chosen}"
    else:
        # Cloud: 3-way intent routing (Z.AI, Groq, OpenAI, DeepSeek, etc.)
        current = request_obj.model
        prefix = current.rsplit("/", 1)[0] if "/" in current else "openai"

        if intent == "CHAT" and small_model != big_model:
            request_obj.model = f"{prefix}/{small_model}"
        elif intent == "BUILDING" and building_model != big_model:
            request_obj.model = f"{prefix}/{building_model}"
        # PLANNING (and fallback): stays on big_model (already mapped)

    print("[route] approx_tokens=", approx_tokens,
      "intent=", intent,
      "provider=", preferred_provider,
      "is_ollama=", is_ollama_base(openai_base_url),
      "model_in=", getattr(request_obj, "original_model", None) or "n/a",
      "model_out=", request_obj.model,
      "tools_in=", len(getattr(request_obj, "tools", []) or []),
      "dropped=", dropped)

    return approx_tokens, dropped

async def _call_provider(request_obj: Any, litellm_request: dict) -> Tuple[bool, Any]:
    """Execute a single litellm call. For streaming, validates the first chunk."""
    

    if getattr(request_obj, "stream", False):
        gen = await litellm.acompletion(**litellm_request)
        # Consume first chunk to validate the connection before committing
        first_chunk = await gen.__anext__()

        # Track cache hit from first chunk
        hidden = getattr(first_chunk, "_hidden_params", {}) or {}
        if hidden.get("cache_hit"):
            metrics.cache_hits += 1
        else:
            metrics.cache_misses += 1

        async def _chain(first, rest):
            yield first
            async for chunk in rest:
                yield chunk

        return True, _chain(first_chunk, gen)

    resp = litellm.completion(**litellm_request)

    # Track cache hit from response
    hidden = getattr(resp, "_hidden_params", {}) or {}
    if hidden.get("cache_hit"):
        metrics.cache_hits += 1
    else:
        metrics.cache_misses += 1

    return False, resp


def _inject_credentials(
    litellm_request: dict,
    *,
    model: str,
    openai_api_key: str,
    openai_base_url: Optional[str],
    anthropic_api_key: Optional[str],
    gemini_api_key: Optional[str],
    use_vertex_auth: bool,
    vertex_project: str,
    vertex_location: str,
) -> None:
    """Inject provider credentials into a litellm request dict (primary path)."""
    if model.startswith("openai/"):
        litellm_request["api_key"] = openai_api_key
        if openai_base_url:
            litellm_request["api_base"] = openai_base_url
    elif model.startswith("gemini/"):
        if use_vertex_auth:
            litellm_request["vertex_project"] = vertex_project
            litellm_request["vertex_location"] = vertex_location
            litellm_request["custom_llm_provider"] = "vertex_ai"
        else:
            litellm_request["api_key"] = gemini_api_key
    else:
        litellm_request["api_key"] = anthropic_api_key


async def run_messages(
    *,
    request_obj: Any,
    openai_api_key: str,
    openai_base_url: Optional[str],
    anthropic_api_key: Optional[str],
    gemini_api_key: Optional[str],
    use_vertex_auth: bool,
    vertex_project: str,
    vertex_location: str,
    fallback_providers: list | None = None,
    intent: str = "CHAT",
) -> Tuple[bool, Any, str]:
    """
    Returns: (is_streaming, response_or_generator, provider_name)
    Tries primary provider first, then fallbacks sequentially.
    For streaming, validates the first chunk before committing.
    """
    # --- Primary provider (uses model already set by apply_policy_and_routing) ---
    model = str(getattr(request_obj, "model", "") or "")
    litellm_request = convert_anthropic_to_litellm(request_obj)
    _inject_credentials(
        litellm_request, model=model,
        openai_api_key=openai_api_key, openai_base_url=openai_base_url,
        anthropic_api_key=anthropic_api_key, gemini_api_key=gemini_api_key,
        use_vertex_auth=use_vertex_auth, vertex_project=vertex_project,
        vertex_location=vertex_location,
    )

    if not fallback_providers:
        is_stream, out = await _call_provider(request_obj, litellm_request)
        return is_stream, out, "primary"

    # --- With fallback chain ---
    try:
        is_stream, out = await _call_provider(request_obj, litellm_request)
        return is_stream, out, "primary"
    except Exception as primary_err:
        print(f"[fallback] primary failed: {primary_err}")

    errors = [f"primary: {primary_err}"]
    original_model = getattr(request_obj, "original_model", None) or model

    for provider in fallback_providers:
        try:
            request_obj.model = provider.get_litellm_model(intent)
            fb_request = convert_anthropic_to_litellm(request_obj)
            fb_request["api_key"] = provider.api_key
            if provider.base_url:
                fb_request["api_base"] = provider.base_url

            print(f"[fallback] trying {provider.name}: model={request_obj.model}")
            is_stream, out = await _call_provider(request_obj, fb_request)
            return is_stream, out, provider.name
        except Exception as e:
            print(f"[fallback] {provider.name} failed: {e}")
            errors.append(f"{provider.name}: {e}")
            continue

    # Restore original model for error reporting
    request_obj.model = original_model
    raise Exception(f"All providers failed: {'; '.join(errors)}")

