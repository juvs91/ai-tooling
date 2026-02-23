# app/proxy/proxy.py
from __future__ import annotations
import asyncio
import json
from typing import Any, Optional, Tuple

import litellm
import os
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


# ── Tool usage enforcement ──────────────────────────────────────────
# Injected when ANALYSIS_ENFORCEMENT=1 AND is_analysis_request() matches.
# Dynamically reads tools from the request — no hardcoded tool names.

def _build_tool_enforcement_prompt(tools: list | None) -> str:
    """Build a dynamic prompt from the request's actual tools. No maintenance needed."""
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
    is_analysis: bool = False,
) -> Tuple[int, list[str]]:
    # Guardar modelo original para logging/debug
    if not getattr(request_obj, "original_model", None):
        setattr(request_obj, "original_model", getattr(request_obj, "model", None))


    # 1) Guardrail system note (SIN system_content_cls)
    ensure_system_note(request_obj, BASE_GUARD_SYSTEM)

    # 1b) Tool enforcement for analysis requests
    if is_analysis:
        tool_prompt = _build_tool_enforcement_prompt(getattr(request_obj, "tools", None))
        if tool_prompt:
            ensure_system_note(request_obj, tool_prompt)
            print("[analysis-guard] Injected tool enforcement prompt")

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

    if dropped and policy_note_in_system:
        ensure_system_note(
            request_obj,
            f"[proxy-policy] Tools not allowed and were removed: {', '.join(dropped)}. Allowed: {', '.join(sorted(allow))}."
        )

    # 4) Model mapping (haiku/sonnet/opus -> provider prefix + big/small)
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
        # Note: server.py already overrides CHAT→BUILDING when tools >= TOOL_UPGRADE_THRESHOLD
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


def _is_retryable_error(error: Exception) -> bool:
    """Check if error should trigger a retry.

    Retryable: rate limits, timeouts, connection issues, transient server errors.
    NOT retryable: context window exceeded, bad request, auth (same input always fails).
    """
    # Never retry context window / bad request — same payload will always fail
    if isinstance(error, (ContextWindowExceededError, LiteLLMBadRequestError)):
        return False

    # Typed retryable exceptions
    if isinstance(error, (LiteLLMRateLimitError, LiteLLMTimeout, LiteLLMAPIConnectionError,
                          LiteLLMServiceUnavailableError, LiteLLMInternalServerError)):
        return True

    # String fallback for non-LiteLLM exceptions
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
                delay = base_delay * (2 ** attempt)  # 1s, 2s, 4s, 8s, 16s
                print(f"[retry] Attempt {attempt + 1}/{max_retries} failed, retry in {delay}s: {type(e).__name__}")
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
    openai_api_key: str,
    openai_base_url: Optional[str],
    anthropic_api_key: Optional[str],
    anthropic_base_url: Optional[str] = None,
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
    else:  # anthropic/ prefix (or bare model names)
        litellm_request["api_key"] = anthropic_api_key
        if anthropic_base_url:
            litellm_request["api_base"] = anthropic_base_url


async def run_messages(
    *,
    request_obj: Any,
    openai_api_key: str,
    openai_base_url: Optional[str],
    anthropic_api_key: Optional[str],
    anthropic_base_url: Optional[str] = None,
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
    model_ctx = int(os.environ.get("MODEL_CONTEXT_WINDOW", "0"))
    litellm_request = convert_anthropic_to_litellm(request_obj, model_context_window=model_ctx)

    # Provider-specific extra_body params (e.g. Z.AI requires tool_stream=True)
    # Configured via STREAM_EXTRA_BODY env var (JSON object), applied to all streaming+tools requests.
    # Example: STREAM_EXTRA_BODY={"tool_stream": true}
    _extra_raw = os.environ.get("STREAM_EXTRA_BODY", "").strip()
    if _extra_raw and litellm_request.get("stream") and litellm_request.get("tools"):
        try:
            extra_params = json.loads(_extra_raw)
            if isinstance(extra_params, dict) and extra_params:
                litellm_request.setdefault("extra_body", {}).update(extra_params)
                print(f"[tools] Applied STREAM_EXTRA_BODY: {list(extra_params.keys())}")
        except json.JSONDecodeError:
            print(f"[tools] WARNING: STREAM_EXTRA_BODY is not valid JSON: {_extra_raw[:100]}")

    # --- Context compression if needed ---
    if model_ctx > 0:
        comp_model = (os.environ.get("COMPRESSOR_MODEL", "").strip()
                      or os.environ.get("CLASSIFIER_MODEL", "").strip())
        comp_key = (os.environ.get("COMPRESSOR_API_KEY", "").strip()
                    or os.environ.get("CLASSIFIER_API_KEY", "").strip())
        comp_base = (os.environ.get("COMPRESSOR_BASE_URL", "").strip()
                     or os.environ.get("CLASSIFIER_BASE_URL", "").strip() or None)
        keep_recent = int(os.environ.get("COMPRESSOR_KEEP_RECENT", "15"))

        if comp_model and comp_key:

            trigger_ratio = float(os.environ.get("COMPRESSOR_TRIGGER_RATIO", "0.85"))
            tools_overhead = estimate_tools_tokens(litellm_request.get("tools"))

            # Fallback compressor (tried if primary fails after retries)
            fb_model = os.environ.get("COMPRESSOR_FALLBACK_MODEL", "").strip() or None
            fb_key = os.environ.get("COMPRESSOR_FALLBACK_API_KEY", "").strip() or None
            fb_base = os.environ.get("COMPRESSOR_FALLBACK_BASE_URL", "").strip() or None

            litellm_request["messages"], was_compressed = await compress_messages_if_needed(
                messages=litellm_request["messages"],
                model_context_window=model_ctx,
                compressor_model=comp_model,
                compressor_api_key=comp_key,
                compressor_base_url=comp_base,
                keep_recent=keep_recent,
                trigger_ratio=trigger_ratio,
                tools_overhead_tokens=tools_overhead,
                target_model=model,
                fallback_model=fb_model,
                fallback_api_key=fb_key,
                fallback_base_url=fb_base,
            )

            # Recalculate max_completion_tokens after compression.
            # The initial cap in convert_anthropic_to_litellm() uses pre-compression
            # message sizes — when input exceeds the context window, remaining goes
            # negative and dynamic_cap floors to 1024.  After compression shrinks
            # messages, recalculate so the model gets adequate output budget.
            if was_compressed and model.startswith("openai/") and not is_no_tools_model(model):
                provider_max = int(os.environ.get("MAX_OUTPUT_TOKENS", "8192"))
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

    _inject_credentials(
        litellm_request, model=model,
        openai_api_key=openai_api_key, openai_base_url=openai_base_url,
        anthropic_api_key=anthropic_api_key, anthropic_base_url=anthropic_base_url,
        gemini_api_key=gemini_api_key,
        use_vertex_auth=use_vertex_auth, vertex_project=vertex_project,
        vertex_location=vertex_location,
    )

    max_retries = int(os.environ.get("MAX_RETRIES", "5"))
    base_delay = float(os.environ.get("RETRY_BASE_DELAY", "1.0"))

    if not fallback_providers:
        is_stream, out = await _call_provider_with_retry(
            request_obj, litellm_request, max_retries=max_retries, base_delay=base_delay,
        )
        return is_stream, out, "primary"

    # --- With fallback chain: retry primary, then fallbacks ---
    primary_error_msg = ""
    try:
        is_stream, out = await _call_provider_with_retry(
            request_obj, litellm_request, max_retries=max_retries, base_delay=base_delay,
        )
        return is_stream, out, "primary"
    except Exception as primary_err:
        primary_error_msg = str(primary_err)
        print(f"[fallback] primary failed after {max_retries} attempts: {primary_error_msg}")

    errors = [f"primary: {primary_error_msg}"]
    original_model = getattr(request_obj, "original_model", None) or model

    for provider in fallback_providers:
        try:
            request_obj.model = provider.get_litellm_model(intent)
            fb_ctx = getattr(provider, "context_window", 0) or model_ctx
            fb_request = convert_anthropic_to_litellm(request_obj, model_context_window=fb_ctx)
            fb_request["api_key"] = provider.api_key
            if provider.base_url:
                fb_request["api_base"] = provider.base_url

            print(f"[fallback] trying {provider.name}: model={request_obj.model}")
            is_stream, out = await _call_provider_with_retry(
                request_obj, fb_request, max_retries=max_retries, base_delay=base_delay,
            )
            return is_stream, out, provider.name
        except Exception as e:
            print(f"[fallback] {provider.name} failed: {e}")
            errors.append(f"{provider.name}: {e}")
            continue

    # Restore original model for error reporting
    request_obj.model = original_model
    raise Exception(f"All providers failed: {'; '.join(errors)}")

