from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import time
import logging
import warnings
from datetime import datetime, timezone
from dotenv import load_dotenv

# Suppress LiteLLM's Pydantic serialization warnings (cosmetic, from internal validation)
warnings.filterwarnings("ignore", message="Pydantic serializer warnings", category=UserWarning)

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
from llm.schemas import MessagesRequest, TokenCountRequest, TokenCountResponse
from llm.streaming import handle_streaming
from router.model_mapper import map_claude_alias_to_target
from router.llm_router import classify_intent, get_last_user_text, _regex_fallback_intent, is_analysis_request
from utils.metrics import metrics, RequestLog
from config import load_config

load_dotenv()
logger = logging.getLogger(__name__)
app = FastAPI()

# ── Load all config once at startup ──
cfg = load_config()

# Suppress LiteLLM's red "Provider List" / "Give Feedback" banners on unknown models
litellm.suppress_debug_info = True

# Skip LLM classifier when all models are identical (no routing benefit)
_models_differ = (
    cfg.routing.big_model != cfg.routing.small_model
    or cfg.routing.building_model != cfg.routing.big_model
)

# Startup validation: warn if classifier is half-configured
if cfg.classifier.model and not cfg.classifier.api_key:
    logger.warning(
        "[startup] CLASSIFIER_MODEL=%s is set but CLASSIFIER_API_KEY is empty. "
        "LLM classifier will fail and fall back to regex. "
        "Set CLASSIFIER_API_KEY or remove CLASSIFIER_MODEL.",
        cfg.classifier.model,
    )
if cfg.classifier.model and _models_differ:
    logger.info(
        "[startup] Intent classifier: model=%s base=%s timeout=%.1fs",
        cfg.classifier.model, cfg.classifier.base_url or "(default)", cfg.classifier.timeout,
    )
elif cfg.classifier.model and not _models_differ:
    logger.info(
        "[startup] Intent classifier: SKIPPED (all models identical: %s) — using regex fallback",
        cfg.routing.big_model,
    )
else:
    logger.info("[startup] Intent classifier: regex fallback (CLASSIFIER_MODEL not set)")

# Initialize LiteLLM response cache
if cfg.cache_enabled:
    litellm.cache = litellm.Cache(type="local", ttl=cfg.cache_ttl)
    litellm.enable_cache()
    logger.info("[startup] Response cache: ENABLED (TTL=%ds, in-memory)", cfg.cache_ttl)
else:
    logger.info("[startup] Response cache: disabled (set CACHE_ENABLED=1 to enable)")

# Retry configuration
logger.info("[startup] Retry: max_retries=%d, base_delay=%.1fs", cfg.max_retries, cfg.retry_base_delay)

# Fallback chain
if cfg.fallback_providers:
    logger.info(
        "[startup] Fallback chain: %s",
        " → ".join(p.name + "(" + p.provider_prefix + "/" + p.big_model + ")" for p in cfg.fallback_providers),
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
        # Skip LLM classifier when all models are identical (no routing benefit)
        last_text = get_last_user_text(request.messages)
        if cfg.classifier.model and _models_differ:
            intent = await classify_intent(
                last_text,
                model=cfg.classifier.model,
                api_key=cfg.classifier.api_key,
                api_base=cfg.classifier.base_url,
                timeout_s=cfg.classifier.timeout,
            )
        else:
            intent = _regex_fallback_intent(last_text)

        # Override: many tools = agentic behavior, never downgrade to CHAT/PLANNING
        tools_count = len(getattr(request, "tools", None) or [])
        if intent in ("CHAT", "PLANNING") and tools_count >= cfg.policy.tool_upgrade_threshold:
            original_intent = intent
            intent = "BUILDING"
            print(f"[classify] {original_intent}→BUILDING override: {tools_count} tools >= {cfg.policy.tool_upgrade_threshold}")

        # Analysis detection (only when toggle is on)
        is_analysis = cfg.policy.analysis_enforcement and is_analysis_request(last_text)

        # policy + routing
        try:
            apply_policy_and_routing(
                request_obj=request,
                raw_body=body,
                cfg=cfg,
                intent=intent,
                is_analysis=is_analysis,
            )
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e))

        t0 = time.monotonic()
        original_model = getattr(request, "original_model", "") or ""

        # ── Standard path (LiteLLM handles all providers: openai/, anthropic/, gemini/) ──
        is_stream, out, provider_used = await run_messages(
            request_obj=request,
            cfg=cfg,
            intent=intent,
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if is_stream:
            metrics.record(RequestLog(
                timestamp=datetime.now(timezone.utc).isoformat(),
                intent=intent,
                model_requested=original_model,
                model_used=request.model,
                provider=provider_used,
                input_tokens=0, output_tokens=0,
                latency_ms=elapsed_ms,
                is_fallback=provider_used != "primary",
                is_stream=True, is_analysis=is_analysis,
            ))
            return StreamingResponse(
                handle_streaming(
                    out, request,
                    model_context_window=cfg.routing.model_context_window,
                    classifier_model=cfg.classifier.model,
                    classifier_api_key=cfg.classifier.api_key,
                    classifier_base_url=cfg.classifier.base_url,
                ),
                media_type="text/event-stream",
            )

        anthropic_response = convert_litellm_to_anthropic(
            out, request, model_context_window=cfg.routing.model_context_window,
        )

        input_tokens = getattr(anthropic_response, "usage", None)
        in_tok = input_tokens.input_tokens if input_tokens else 0
        out_tok = input_tokens.output_tokens if input_tokens else 0

        metrics.record(RequestLog(
            timestamp=datetime.now(timezone.utc).isoformat(),
            intent=intent,
            model_requested=original_model,
            model_used=request.model,
            provider=provider_used,
            input_tokens=in_tok, output_tokens=out_tok,
            latency_ms=elapsed_ms,
            is_fallback=provider_used != "primary",
            is_stream=False, is_analysis=is_analysis,
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
            preferred_provider=cfg.routing.preferred_provider,
            big_model=cfg.routing.big_model,
            small_model=cfg.routing.small_model,
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

        return TokenCountResponse(input_tokens=scale_tokens(input_tokens, cfg.routing.model_context_window))

    except Exception as e:
        logger.error(f"Token counting error: {e}")
        raise HTTPException(status_code=500, detail=f"Token counting failed: {str(e)}")


@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": cfg.routing.preferred_provider,
        "models": {
            "small": cfg.routing.small_model,
            "big": cfg.routing.big_model,
            "building": cfg.routing.building_model,
        },
        "classifier": {
            "mode": "llm" if cfg.classifier.model else "regex",
            "model": cfg.classifier.model or None,
            "base_url": cfg.classifier.base_url,
            "timeout_s": cfg.classifier.timeout,
        },
        "fallbacks": [
            {"name": f.name, "provider": f.provider_prefix, "big": f.big_model}
            for f in cfg.fallback_providers
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
