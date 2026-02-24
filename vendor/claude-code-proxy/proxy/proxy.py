# app/proxy/proxy.py
from __future__ import annotations
import asyncio
import json
from typing import Any, Optional, Tuple

import litellm
from litellm.exceptions import (
    ContextWindowExceededError,
    BadRequestError as LiteLLMBadRequestError,
    RateLimitError as LiteLLMRateLimitError,
    Timeout as LiteLLMTimeout,
    APIConnectionError as LiteLLMAPIConnectionError,
    ServiceUnavailableError as LiteLLMServiceUnavailableError,
    InternalServerError as LiteLLMInternalServerError,
)
from llm.compressor import estimate_tools_tokens
from utils.utils import (
    approx_tokens_from_bytes,
    get_tool_name,
    parse_allowlist,
    ensure_system_note,
    filter_tools_allowlist,
    normalize_tool_choice,
)
from utils.metrics import metrics
from router.llm_router import choose_local_model
from llm.converters import convert_anthropic_to_litellm
from llm.compressor import compress_messages_if_needed
from llm.tool_prompting import is_no_tools_model
from router.model_mapper import map_claude_alias_to_target

from config import ProxyConfig, ProviderCredentials


# ── Tool usage enforcement ──────────────────────────────────────────
# Injected when ANALYSIS_ENFORCEMENT=1 AND is_analysis_request() matches.

def _build_tool_enforcement_prompt(tools: list | None) -> str:
    """Build a dynamic prompt from the request's actual tools."""
    if not tools:
        return ""
    tool_names = []
    for t in (tools or []):
        name = get_tool_name(t)
        if name:
            tool_names.append(name)
    if not tool_names:
        return ""
    return (
        f"[tool-guard] You have {len(tool_names)} tools available: {', '.join(tool_names)}\n"
        "You MUST use these tools to gather real data before answering.\n"
        "Do NOT answer from memory when a tool can provide actual information.\n"
        "If a tool fails or is unavailable, say so explicitly — do NOT fabricate.\n"
        "Cite sources (file:line) for factual claims."
    )

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
    if "api.groq.com" in b or "groq.com" in b:
        return 5500
    if "11434" in b:
        return 25000
    return 0


def apply_policy_and_routing(
    *,
    request_obj: Any,
    raw_body: bytes,
    cfg: ProxyConfig,
    intent: str = "CHAT",
    is_analysis: bool = False,
) -> Tuple[int, list[str]]:
    # Save original model for logging/debug
    if not getattr(request_obj, "original_model", None):
        setattr(request_obj, "original_model", getattr(request_obj, "model", None))

    # 1) Guardrail system note
    ensure_system_note(request_obj, cfg.policy.guard_system)

    # 1b) Tool enforcement for analysis requests
    if is_analysis:
        tool_prompt = _build_tool_enforcement_prompt(getattr(request_obj, "tools", None))
        if tool_prompt:
            ensure_system_note(request_obj, tool_prompt)
            print("[analysis-guard] Injected tool enforcement prompt")

    approx_tokens = approx_tokens_from_bytes(raw_body)

    # Provider-specific cap (before LiteLLM)
    cap = provider_cap_for_base_url(cfg.credentials.openai_base_url)
    if cap and approx_tokens > cap:
        msg = (
            f"[proxy-policy] Provider cap exceeded: approx_tokens={approx_tokens} > cap={cap} "
            f"(base_url={cfg.credentials.openai_base_url}). Reduce context or use another provider."
        )
        if cfg.policy.hard_block_oversize:
            raise ValueError(msg)

    # 2) Hard cap
    if cfg.policy.max_input_tokens > 0 and approx_tokens > cfg.policy.max_input_tokens:
        msg = (
            f"[proxy-policy] Oversize request: approx_tokens={approx_tokens} > "
            f"MAX_INPUT_TOKENS={cfg.policy.max_input_tokens}. Reduce workspace/context."
        )
        if cfg.policy.hard_block_oversize:
            raise ValueError(msg)

    # 3) Tools allowlist
    allow = parse_allowlist(cfg.policy.tool_allowlist_raw)
    if not allow:
        dropped = []
        for t in (getattr(request_obj, "tools", None) or []):
            name = get_tool_name(t)
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

    if dropped and cfg.policy.policy_note_in_system:
        ensure_system_note(
            request_obj,
            f"[proxy-policy] Tools not allowed and were removed: {', '.join(dropped)}. Allowed: {', '.join(sorted(allow))}."
        )

    # 4) Model mapping (haiku/sonnet/opus -> provider prefix + big/small)
    request_obj.model = map_claude_alias_to_target(
        getattr(request_obj, "model", ""),
        preferred_provider=cfg.routing.preferred_provider,
        big_model=cfg.routing.big_model,
        small_model=cfg.routing.small_model,
    )

    # 5) Intent-based model override
    if is_ollama_base(cfg.credentials.openai_base_url):
        # Ollama: full scoring with intent
        chosen = choose_local_model(
            messages=getattr(request_obj, "messages", []) or [],
            max_out=int(getattr(request_obj, "max_tokens", 0) or 0),
            approx_tokens=approx_tokens,
            system_chars=system_chars(getattr(request_obj, "system", None)),
            tools_count=len(getattr(request_obj, "tools", None) or []),
            small_model=cfg.routing.small_model,
            big_model=cfg.routing.big_model,
            building_model=cfg.routing.building_model,
            intent=intent,
        )
        request_obj.model = f"openai/{chosen}"
    else:
        # Cloud: 3-way intent routing (Z.AI, Groq, OpenAI, DeepSeek, etc.)
        current = request_obj.model
        prefix = current.rsplit("/", 1)[0] if "/" in current else "openai"

        if intent == "CHAT" and cfg.routing.small_model != cfg.routing.big_model:
            request_obj.model = f"{prefix}/{cfg.routing.small_model}"
        elif intent == "BUILDING" and cfg.routing.building_model != cfg.routing.big_model:
            request_obj.model = f"{prefix}/{cfg.routing.building_model}"
        # PLANNING (and fallback): stays on big_model (already mapped)

    print("[route] approx_tokens=", approx_tokens,
      "intent=", intent,
      "provider=", cfg.routing.preferred_provider,
      "is_ollama=", is_ollama_base(cfg.credentials.openai_base_url),
      "model_in=", getattr(request_obj, "original_model", None) or "n/a",
      "model_out=", request_obj.model,
      "tools_in=", len(getattr(request_obj, "tools", []) or []),
      "dropped=", dropped)

    return approx_tokens, dropped

async def _call_provider(request_obj: Any, litellm_request: dict) -> Tuple[bool, Any]:
    """Execute a single litellm call. For streaming, validates the first chunk."""

    if getattr(request_obj, "stream", False):
        gen = await litellm.acompletion(**litellm_request)
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


def _is_retryable_error(error: Exception) -> bool:
    """Check if error should trigger a retry."""
    if isinstance(error, (ContextWindowExceededError, LiteLLMBadRequestError)):
        return False
    if isinstance(error, (LiteLLMRateLimitError, LiteLLMTimeout, LiteLLMAPIConnectionError,
                          LiteLLMServiceUnavailableError, LiteLLMInternalServerError)):
        return True
    error_str = str(error).lower()
    return (
        "429" in error_str
        or "rate limit" in error_str
        or "timeout" in error_str
        or "connection" in error_str
        or "internal server error" in error_str
    )


async def _call_provider_with_retry(
    request_obj: Any,
    litellm_request: dict,
    max_retries: int = 5,
    base_delay: float = 1.0,
) -> Tuple[bool, Any]:
    """Call provider with exponential backoff on retryable errors."""
    from utils.metrics import metrics

    last_exception = None

    for attempt in range(max_retries):
        try:
            result = await _call_provider(request_obj, litellm_request)
            if attempt > 0:
                metrics.retry_successes += 1
                print(f"[retry] Succeeded on attempt {attempt + 1}/{max_retries}")
            return result
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1 and _is_retryable_error(e):
                delay = base_delay * (2 ** attempt)
                print(f"[retry] Attempt {attempt + 1}/{max_retries} failed, retry in {delay}s: {type(e).__name__}: {str(e)[:200]}")
                metrics.total_retries += 1
                await asyncio.sleep(delay)
            else:
                if not _is_retryable_error(e):
                    print(f"[retry] Non-retryable error on attempt {attempt + 1}: {type(e).__name__}: {str(e)[:200]}")
                raise

    raise last_exception


def _inject_credentials(
    litellm_request: dict,
    *,
    model: str,
    creds: ProviderCredentials,
) -> None:
    """Inject provider credentials into a litellm request dict (primary path)."""
    if model.startswith("openai/"):
        litellm_request["api_key"] = creds.openai_api_key
        if creds.openai_base_url:
            litellm_request["api_base"] = creds.openai_base_url
    elif model.startswith("gemini/"):
        if creds.use_vertex_auth:
            litellm_request["vertex_project"] = creds.vertex_project
            litellm_request["vertex_location"] = creds.vertex_location
            litellm_request["custom_llm_provider"] = "vertex_ai"
        else:
            litellm_request["api_key"] = creds.gemini_api_key
    else:  # anthropic/ prefix (or bare model names)
        litellm_request["api_key"] = creds.anthropic_api_key
        if creds.anthropic_base_url:
            litellm_request["api_base"] = creds.anthropic_base_url


async def run_messages(
    *,
    request_obj: Any,
    cfg: ProxyConfig,
    intent: str = "CHAT",
) -> Tuple[bool, Any, str]:
    """
    Returns: (is_streaming, response_or_generator, provider_name)
    Tries primary provider first, then fallbacks sequentially.
    """
    model = str(getattr(request_obj, "model", "") or "")
    model_ctx = cfg.routing.model_context_window
    litellm_request = convert_anthropic_to_litellm(
        request_obj, model_context_window=model_ctx, max_output_tokens=cfg.routing.max_output_tokens,
    )

    # Provider-specific extra_body params (e.g. Z.AI requires tool_stream=True)
    if cfg.stream_extra_body and litellm_request.get("stream") and litellm_request.get("tools"):
        litellm_request.setdefault("extra_body", {}).update(cfg.stream_extra_body)
        print(f"[tools] Applied STREAM_EXTRA_BODY: {list(cfg.stream_extra_body.keys())}")

    # --- Context compression if needed ---
    comp = cfg.compressor
    if model_ctx > 0 and comp.model and comp.api_key:
        trigger_ratio = comp.trigger_ratio
        tools_overhead = estimate_tools_tokens(litellm_request.get("tools"))

        litellm_request["messages"], was_compressed = await compress_messages_if_needed(
            messages=litellm_request["messages"],
            model_context_window=model_ctx,
            compressor_model=comp.model,
            compressor_api_key=comp.api_key,
            compressor_base_url=comp.base_url,
            keep_recent=comp.keep_recent,
            trigger_ratio=trigger_ratio,
            tools_overhead_tokens=tools_overhead,
            target_model=model,
            fallback_model=comp.fallback_model,
            fallback_api_key=comp.fallback_api_key,
            fallback_base_url=comp.fallback_base_url,
        )

        # Recalculate max_completion_tokens after compression
        if was_compressed and model.startswith("openai/") and not is_no_tools_model(model):
            provider_max = cfg.routing.max_output_tokens
            input_est = sum(len(str(m.get("content", ""))) for m in litellm_request["messages"]) // 4
            tools_est = sum(
                len(json.dumps(t)) // 4 for t in (litellm_request.get("tools") or [])
            )
            remaining = model_ctx - input_est - tools_est
            safe = int(remaining * 0.85)
            new_cap = max(1024, min(safe, provider_max))
            new_max = min(request_obj.max_tokens, new_cap)
            old_max = litellm_request.get("max_completion_tokens", new_max)
            if new_max != old_max:
                print(f"[compress] Recapped max_tokens: {old_max} → {new_max} "
                      f"(post-compression input~{input_est} tools~{tools_est} remaining~{remaining})")
            litellm_request["max_completion_tokens"] = new_max

    _inject_credentials(litellm_request, model=model, creds=cfg.credentials)

    if not cfg.fallback_providers:
        is_stream, out = await _call_provider_with_retry(
            request_obj, litellm_request,
            max_retries=cfg.max_retries, base_delay=cfg.retry_base_delay,
        )
        return is_stream, out, "primary"

    # --- With fallback chain: retry primary, then fallbacks ---
    primary_error_msg = ""
    try:
        is_stream, out = await _call_provider_with_retry(
            request_obj, litellm_request,
            max_retries=cfg.max_retries, base_delay=cfg.retry_base_delay,
        )
        return is_stream, out, "primary"
    except Exception as primary_err:
        primary_error_msg = str(primary_err)
        print(f"[fallback] primary failed after {cfg.max_retries} attempts: {primary_error_msg}")

    errors = [f"primary: {primary_error_msg}"]
    original_model = getattr(request_obj, "original_model", None) or model

    for provider in cfg.fallback_providers:
        try:
            request_obj.model = provider.get_litellm_model(intent)
            fb_ctx = getattr(provider, "context_window", 0) or model_ctx
            fb_request = convert_anthropic_to_litellm(
                request_obj, model_context_window=fb_ctx, max_output_tokens=cfg.routing.max_output_tokens,
            )
            fb_request["api_key"] = provider.api_key
            if provider.base_url:
                fb_request["api_base"] = provider.base_url

            print(f"[fallback] trying {provider.name}: model={request_obj.model}")
            is_stream, out = await _call_provider_with_retry(
                request_obj, fb_request,
                max_retries=cfg.max_retries, base_delay=cfg.retry_base_delay,
            )
            return is_stream, out, provider.name
        except Exception as e:
            print(f"[fallback] {provider.name} failed: {e}")
            errors.append(f"{provider.name}: {e}")
            continue

    # Restore original model for error reporting
    request_obj.model = original_model
    raise Exception(f"All providers failed: {'; '.join(errors)}")
