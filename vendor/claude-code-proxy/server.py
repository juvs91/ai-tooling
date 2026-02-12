from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import os
import json
import time
import logging
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv

import litellm
from litellm import token_counter
from litellm.exceptions import (
    ContextWindowExceededError,
    BadRequestError as LiteLLMBadRequestError,
    AuthenticationError as LiteLLMAuthenticationError,
    RateLimitError as LiteLLMRateLimitError,
    Timeout as LiteLLMTimeout,
    APIConnectionError as LiteLLMAPIConnectionError,
    ServiceUnavailableError as LiteLLMServiceUnavailableError,
    InternalServerError as LiteLLMInternalServerError,
    NotFoundError as LiteLLMNotFoundError,
    ContentPolicyViolationError,
)
from utils.utils import cached_token_count, store_token_count, scale_tokens
from proxy.proxy import apply_policy_and_routing, run_messages
from llm.converters import convert_litellm_to_anthropic
from llm.schemas import MessagesRequest, TokenCountRequest, TokenCountResponse, ProviderConfig
from llm.streaming import handle_streaming
from router.model_mapper import map_claude_alias_to_target
from router.llm_router import classify_intent, get_last_user_text, _regex_fallback_intent
from utils.metrics import metrics, RequestLog
load_dotenv()
logger = logging.getLogger(__name__)
app = FastAPI()

# envs
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
VERTEX_PROJECT = os.environ.get("VERTEX_PROJECT", "unset")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "unset")
USE_VERTEX_AUTH = os.environ.get("USE_VERTEX_AUTH", "False").lower() == "true"

TOOL_ALLOWLIST = os.environ.get("TOOL_ALLOWLIST", "").strip()
POLICY_NOTE_IN_SYSTEM = os.environ.get("POLICY_NOTE_IN_SYSTEM", "1").strip() == "1"
MAX_INPUT_TOKENS = int(os.environ.get("MAX_INPUT_TOKENS", "0"))
HARD_BLOCK_OVERSIZE = os.environ.get("HARD_BLOCK_OVERSIZE", "0").strip() == "1"
PREFERRED_PROVIDER = os.environ.get("PREFERRED_PROVIDER", "openai").lower()

SMALL_MODEL = os.environ.get("SMALL_MODEL", "cc-local:chat")
BIG_MODEL = os.environ.get("BIG_MODEL", SMALL_MODEL)
BUILDING_MODEL = os.environ.get("BUILDING_MODEL", BIG_MODEL)

# Context window scaling: Claude Code assumes 200K. If your model has a smaller
# window, set MODEL_CONTEXT_WINDOW so token counts are scaled proportionally.
# Set to 0 to disable scaling (pass through raw counts).
MODEL_CONTEXT_WINDOW = int(os.environ.get("MODEL_CONTEXT_WINDOW", "0"))

# Intent classifier config - use a cheap model (e.g. deepseek-chat)
# Format: "provider/model" e.g. "openai/deepseek-chat"
# Leave empty to use regex fallback (no LLM call)
CLASSIFIER_MODEL = os.environ.get("CLASSIFIER_MODEL", "").strip()
CLASSIFIER_API_KEY = os.environ.get("CLASSIFIER_API_KEY", "").strip()
CLASSIFIER_BASE_URL = os.environ.get("CLASSIFIER_BASE_URL", "").strip() or None
CLASSIFIER_TIMEOUT = float(os.environ.get("CLASSIFIER_TIMEOUT", "3.0"))

# Response cache config — in-memory, reduces duplicate API calls on retries/bursts
CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "0").strip() == "1"
CACHE_TTL = int(os.environ.get("CACHE_TTL", "60"))

# Startup validation: warn if classifier is half-configured
if CLASSIFIER_MODEL and not CLASSIFIER_API_KEY:
    logger.warning(
        "[startup] CLASSIFIER_MODEL=%s is set but CLASSIFIER_API_KEY is empty. "
        "LLM classifier will fail and fall back to regex. "
        "Set CLASSIFIER_API_KEY or remove CLASSIFIER_MODEL.",
        CLASSIFIER_MODEL,
    )
if CLASSIFIER_MODEL:
    logger.info(
        "[startup] Intent classifier: model=%s base=%s timeout=%.1fs",
        CLASSIFIER_MODEL, CLASSIFIER_BASE_URL or "(default)", CLASSIFIER_TIMEOUT,
    )
else:
    logger.info("[startup] Intent classifier: regex fallback (CLASSIFIER_MODEL not set)")

# Initialize LiteLLM response cache
if CACHE_ENABLED:
    litellm.cache = litellm.Cache(type="local", ttl=CACHE_TTL)
    litellm.enable_cache()
    logger.info("[startup] Response cache: ENABLED (TTL=%ds, in-memory)", CACHE_TTL)
else:
    logger.info("[startup] Response cache: disabled (set CACHE_ENABLED=1 to enable)")

# Retry configuration
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))
RETRY_BASE_DELAY = float(os.environ.get("RETRY_BASE_DELAY", "1.0"))
logger.info("[startup] Retry: max_retries=%d, base_delay=%.1fs", MAX_RETRIES, RETRY_BASE_DELAY)

# Fallback provider chain (FALLBACK_1_*, FALLBACK_2_*, ... FALLBACK_9_*)
def _load_fallback_providers() -> list[ProviderConfig]:
    providers = []
    for n in range(1, 10):
        prefix = f"FALLBACK_{n}_"
        provider = os.environ.get(f"{prefix}PROVIDER", "").strip()
        api_key = os.environ.get(f"{prefix}API_KEY", "").strip()
        if not provider or not api_key:
            break  # stop at first gap
        providers.append(ProviderConfig(
            name=f"fallback_{n}",
            provider_prefix=provider,
            api_key=api_key,
            base_url=os.environ.get(f"{prefix}BASE_URL", "").strip() or None,
            big_model=os.environ.get(f"{prefix}BIG_MODEL", "").strip(),
            small_model=os.environ.get(f"{prefix}SMALL_MODEL", "").strip() or os.environ.get(f"{prefix}BIG_MODEL", "").strip(),
            building_model=os.environ.get(f"{prefix}BUILDING_MODEL", "").strip() or None,
            context_window=int(os.environ.get(f"{prefix}CONTEXT_WINDOW", "0")),
        ))
    return providers

FALLBACK_PROVIDERS = _load_fallback_providers()
if FALLBACK_PROVIDERS:
    logger.info(
        "[startup] Fallback chain: %s",
        " → ".join(p.name + "(" + p.provider_prefix + "/" + p.big_model + ")" for p in FALLBACK_PROVIDERS),
    )
else:
    logger.info("[startup] No fallback providers configured")

def _classify_llm_error(e: Exception) -> tuple[int, str]:
    """Map exception to (HTTP status, detail). Uses LiteLLM typed exceptions first."""
    error_str = str(e)

    # LiteLLM typed exceptions (most specific first)
    if isinstance(e, ContextWindowExceededError):
        return 400, f"Context window exceeded: {error_str[:300]}"
    if isinstance(e, ContentPolicyViolationError):
        return 400, f"Content policy violation: {error_str[:300]}"
    if isinstance(e, LiteLLMAuthenticationError):
        return 401, f"Authentication failed: {error_str[:300]}"
    if isinstance(e, LiteLLMRateLimitError):
        return 429, f"Rate limited: {error_str[:300]}"
    if isinstance(e, LiteLLMNotFoundError):
        return 404, f"Model not found: {error_str[:300]}"
    if isinstance(e, LiteLLMTimeout):
        return 504, f"Upstream timeout: {error_str[:300]}"
    if isinstance(e, LiteLLMAPIConnectionError):
        return 502, f"Connection error: {error_str[:300]}"
    if isinstance(e, LiteLLMServiceUnavailableError):
        return 503, f"Service unavailable: {error_str[:300]}"
    if isinstance(e, LiteLLMInternalServerError):
        return 502, f"Upstream server error: {error_str[:300]}"
    if isinstance(e, LiteLLMBadRequestError):
        return 400, f"Bad request: {error_str[:300]}"

    # Fallback: status_code attribute (some LiteLLM exceptions carry it)
    status_code = getattr(e, "status_code", None)
    if isinstance(status_code, int) and 400 <= status_code < 600:
        return status_code, f"Error ({status_code}): {error_str[:300]}"

    # Last resort: string heuristics for non-LiteLLM exceptions
    lower = error_str.lower()
    if "context" in lower and ("length" in lower or "window" in lower or "exceeded" in lower):
        return 400, f"Context length exceeded: {error_str[:300]}"
    if "400" in error_str:
        return 400, f"Bad request: {error_str[:300]}"
    if "401" in error_str or "authentication" in lower:
        return 401, f"Authentication error: {error_str[:300]}"
    if "429" in error_str or "rate limit" in lower:
        return 429, f"Rate limited: {error_str[:300]}"

    return 500, f"Internal error: {error_str[:300]}"


@app.post("/v1/messages")
async def create_message(request: MessagesRequest, raw_request: Request):
    try:
        body = await raw_request.body()

        # Intent classification (LLM or regex fallback)
        last_text = get_last_user_text(request.messages)
        if CLASSIFIER_MODEL:
            intent = await classify_intent(
                last_text,
                model=CLASSIFIER_MODEL,
                api_key=CLASSIFIER_API_KEY,
                api_base=CLASSIFIER_BASE_URL,
                timeout_s=CLASSIFIER_TIMEOUT,
            )
        else:
            intent = _regex_fallback_intent(last_text)

        # policy + routing
        try:
            apply_policy_and_routing(
                request_obj=request,
                raw_body=body,
                openai_base_url=OPENAI_BASE_URL,
                tool_allowlist_raw=TOOL_ALLOWLIST,
                policy_note_in_system=POLICY_NOTE_IN_SYSTEM,
                max_input_tokens=MAX_INPUT_TOKENS,
                hard_block_oversize=HARD_BLOCK_OVERSIZE,
                small_model=SMALL_MODEL,
                big_model=BIG_MODEL,
                building_model=BUILDING_MODEL,
                preferred_provider=PREFERRED_PROVIDER,
                intent=intent,
            )
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e))

        t0 = time.monotonic()
        original_model = getattr(request, "original_model", "") or ""

        is_stream, out, provider_used = await run_messages(
            request_obj=request,
            openai_api_key=OPENAI_API_KEY,
            openai_base_url=OPENAI_BASE_URL,
            anthropic_api_key=ANTHROPIC_API_KEY,
            gemini_api_key=GEMINI_API_KEY,
            use_vertex_auth=USE_VERTEX_AUTH,
            vertex_project=VERTEX_PROJECT,
            vertex_location=VERTEX_LOCATION,
            fallback_providers=FALLBACK_PROVIDERS or None,
            intent=intent,
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if is_stream:
            # For streaming, record metrics with tokens=0 (actual counts come from SSE)
            metrics.record(RequestLog(
                timestamp=datetime.now(timezone.utc).isoformat(),
                intent=intent,
                model_requested=original_model,
                model_used=request.model,
                provider=provider_used,
                input_tokens=0,
                output_tokens=0,
                latency_ms=elapsed_ms,
                is_fallback=provider_used != "primary",
                is_stream=True,
            ))
            return StreamingResponse(handle_streaming(out, request, model_context_window=MODEL_CONTEXT_WINDOW), media_type="text/event-stream")

        # non-stream: convertir respuesta a Anthropic
        anthropic_response = convert_litellm_to_anthropic(out, request, model_context_window=MODEL_CONTEXT_WINDOW)

        # Extract tokens from non-streaming response
        input_tokens = getattr(anthropic_response, "usage", None)
        in_tok = input_tokens.input_tokens if input_tokens else 0
        out_tok = input_tokens.output_tokens if input_tokens else 0

        metrics.record(RequestLog(
            timestamp=datetime.now(timezone.utc).isoformat(),
            intent=intent,
            model_requested=original_model,
            model_used=request.model,
            provider=provider_used,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=elapsed_ms,
            is_fallback=provider_used != "primary",
            is_stream=False,
        ))

        return anthropic_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "[create_message] %s: %s",
            type(e).__name__, str(e)[:500],
            exc_info=True,
        )
        status, detail = _classify_llm_error(e)
        raise HTTPException(status_code=status, detail=detail)


@app.post("/v1/messages/count_tokens", response_model=TokenCountResponse)
async def count_tokens_endpoint(request: TokenCountRequest):
    """
    Count tokens for Anthropic-style messages.
    Uses LiteLLM token_counter with model mapping.
    """
    try:
        # Map Claude model alias to target provider model
        target_model = map_claude_alias_to_target(
            request.model,
            preferred_provider=PREFERRED_PROVIDER,
            big_model=BIG_MODEL,
            small_model=SMALL_MODEL,
        )

        # Convert Anthropic format to LiteLLM format for counting
        litellm_messages = []

        # Add system message if present
        if request.system:
            system_text = request.system
            if isinstance(request.system, list):
                system_text = "\n".join(
                    b.text if hasattr(b, "text") else b.get("text", "")
                    for b in request.system
                )
            litellm_messages.append({"role": "system", "content": system_text})

        # Convert messages
        for msg in request.messages:
            content = msg.content
            if isinstance(content, str):
                litellm_messages.append({"role": msg.role, "content": content})
            elif isinstance(content, list):
                # Extract text from content blocks
                text_parts = []
                for block in content:
                    if hasattr(block, "text"):
                        text_parts.append(block.text)
                    elif hasattr(block, "type") and block.type == "tool_result":
                        result_content = getattr(block, "content", "")
                        if isinstance(result_content, str):
                            text_parts.append(result_content)
                    elif isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_result":
                            text_parts.append(str(block.get("content", "")))
                if text_parts:
                    litellm_messages.append({"role": msg.role, "content": "\n".join(text_parts)})

        # Check token count cache first
        system_text_for_cache = litellm_messages[0]["content"] if litellm_messages and litellm_messages[0]["role"] == "system" else None
        cached = cached_token_count(litellm_messages, target_model, system_text_for_cache)
        if cached is not None:
            metrics.cache_hits += 1
            input_tokens = cached
        else:
            metrics.cache_misses += 1
            try:
                input_tokens = token_counter(
                    model=target_model,
                    messages=litellm_messages
                )
            except Exception:
                # Fallback: approximate using bytes heuristic
                total_chars = sum(len(str(m.get("content", ""))) for m in litellm_messages)
                input_tokens = max(1, total_chars // 4)
            store_token_count(litellm_messages, target_model, input_tokens, system_text_for_cache)

        return TokenCountResponse(input_tokens=scale_tokens(input_tokens, MODEL_CONTEXT_WINDOW))

    except Exception as e:
        logger.error(f"Token counting error: {e}")
        raise HTTPException(status_code=500, detail=f"Token counting failed: {str(e)}")


@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": PREFERRED_PROVIDER,
        "models": {
            "small": SMALL_MODEL,
            "big": BIG_MODEL,
            "building": BUILDING_MODEL,
        },
        "classifier": {
            "mode": "llm" if CLASSIFIER_MODEL else "regex",
            "model": CLASSIFIER_MODEL or None,
            "base_url": CLASSIFIER_BASE_URL,
            "timeout_s": CLASSIFIER_TIMEOUT,
        },
        "fallbacks": [
            {"name": f.name, "provider": f.provider_prefix, "big": f.big_model}
            for f in FALLBACK_PROVIDERS
        ],
    }


@app.get("/api/stats")
async def get_stats():
    """Aggregated proxy metrics: request counts, latency, fallback rate, cache hits."""
    return metrics.get_stats()


@app.get("/api/logs")
async def get_logs(n: int = 50):
    """Recent request logs (up to 200). Use ?n=100 to control count."""
    return metrics.get_recent(min(n, 200))
