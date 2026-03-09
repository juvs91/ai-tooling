from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import time
import logging
import warnings
from datetime import datetime, timezone

# Configure logging BEFORE any module imports that use getLogger()
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)
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
from proxy.proxy import build_request_pipeline, run_messages
from llm.converters import convert_litellm_to_anthropic, extract_xml_tools_from_passthrough_response
from llm.pipeline import TransformContext
from llm.schemas import MessagesRequest, TokenCountRequest, TokenCountResponse
from llm.streaming import handle_streaming, passthrough_xml_tool_extraction
from router.model_mapper import map_claude_alias_to_target
from utils.metrics import metrics, RequestLog
from llm.stream_quality import (
    extract_response_text,
    score_anthropic_response,
    accumulate_stream,
    analysis_quality_stream,
    tracked_stream,
)
from config import load_config, ProxyConfig

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

# Build the request pipeline once at startup (transformers 1-5: Anthropic-format)
_request_pipeline = build_request_pipeline(cfg, _models_differ)

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

# Analysis config
if cfg.analysis.model:
    logger.info(
        "[startup] Analysis: model=%s base=%s max_tokens=%d refinements=%d",
        cfg.analysis.model, cfg.analysis.base_url or "(default)",
        cfg.analysis.max_tokens, cfg.analysis.max_refinements,
    )
elif cfg.analysis.max_refinements > 0:
    logger.info(
        "[startup] Analysis: refinements=%d (using primary model)",
        cfg.analysis.max_refinements,
    )
else:
    logger.info("[startup] Analysis: no model override, no refinements")

# Safety nets
logger.info(
    "[startup] Safety nets: max_turns=%d cost_warning=$%.2f cost_limit=$%.2f",
    cfg.max_turns, cfg.session_cost_warning, cfg.session_cost_limit,
)

# Passthrough config (auto-detect from anthropic credentials)
if cfg.passthrough_disabled:
    logger.info("[startup] Passthrough: DISABLED (PASSTHROUGH_DISABLED=1)")
elif cfg.credentials.anthropic_base_url:
    logger.info(
        "[startup] Passthrough: AUTO (anthropic endpoints → %s)",
        cfg.credentials.anthropic_base_url,
    )
else:
    logger.info("[startup] Passthrough: N/A (no custom anthropic base_url)")


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
        # Turn limit safety net: prevent runaway sessions
        if cfg.max_turns > 0 and metrics.total_requests >= cfg.max_turns:
            logger.error(
                "[safety] Turn limit reached: %d >= %d. Rejecting request.",
                metrics.total_requests, cfg.max_turns,
            )
            raise HTTPException(
                status_code=429,
                detail=f"Session turn limit reached ({cfg.max_turns} requests). Restart the proxy to reset.",
            )

        # Cost warning: log when approaching or exceeding budget
        if metrics.total_cost_usd > cfg.session_cost_limit:
            logger.error(
                "[budget] LIMIT: $%.2f > $%.2f — consider stopping session",
                metrics.total_cost_usd, cfg.session_cost_limit,
            )
        elif metrics.total_cost_usd > cfg.session_cost_warning:
            logger.warning(
                "[budget] WARNING: $%.2f > $%.2f warning threshold",
                metrics.total_cost_usd, cfg.session_cost_warning,
            )

        body = await raw_request.body()

        # Extract session ID for conversation persistence (Phase 3)
        session_id = raw_request.headers.get("X-Session-ID") or None

        # Phase 1: Request pipeline (classification + guardrails + routing)
        ctx = TransformContext(raw_body=body, session_id=session_id)
        try:
            await _request_pipeline.process(request, ctx)
        except ValueError as e:
            raise HTTPException(status_code=413, detail=str(e))

        t0 = time.monotonic()
        original_model = getattr(request, "original_model", "") or ""

        # Phase 2 + Execution (litellm pipeline runs inside run_messages)
        is_stream, out, provider_used = await run_messages(
            request_obj=request,
            cfg=cfg,
            ctx=ctx,
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # ── Passthrough responses (already Anthropic format, no conversion) ──
        if provider_used == "passthrough":
            est_input_tokens = max(1, len(body) // 6)
            if is_stream:
                # SSE relay — extract GLM argkv tools, then quality loop, then tracked_stream
                stream_gen = out
                if getattr(request, "tools", None):
                    stream_gen = passthrough_xml_tool_extraction(stream_gen, request)
                if ctx.is_analysis and cfg.analysis.max_refinements > 0:
                    stream_gen = analysis_quality_stream(stream_gen, request, ctx, cfg)
                stream_gen = tracked_stream(stream_gen, request, ctx, cfg)
                metrics.record(RequestLog(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    intent=ctx.intent,
                    model_requested=original_model,
                    model_used=request.model,
                    provider="passthrough",
                    input_tokens=est_input_tokens, output_tokens=0,
                    latency_ms=elapsed_ms,
                    is_fallback=False,
                    is_stream=True, is_analysis=ctx.is_analysis,
                    phase=ctx.phase,
                    cost_usd=0.0,
                ))
                metrics.record_model_event(request.model, "request")
                return StreamingResponse(stream_gen, media_type="text/event-stream")
            else:
                # Non-streaming: out is already an Anthropic response dict
                # Extract GLM argkv tool calls from text content (mirrors LiteLLM non-stream path)
                if getattr(request, "tools", None):
                    out = extract_xml_tools_from_passthrough_response(out, request)

                # Extract actual token usage from response (fix for GLM-4.7 metrics)
                usage_info = out.get("usage", {})
                actual_output_tokens = 0
                if isinstance(usage_info, dict):
                    actual_output_tokens = usage_info.get("output_tokens", 0) or usage_info.get("completion_tokens", 0)

                metrics.record(RequestLog(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    intent=ctx.intent,
                    model_requested=original_model,
                    model_used=request.model,
                    provider="passthrough",
                    input_tokens=est_input_tokens, output_tokens=actual_output_tokens,
                    latency_ms=elapsed_ms,
                    is_fallback=False,
                    is_stream=False, is_analysis=ctx.is_analysis,
                    phase=ctx.phase,
                    quality_score=1.0,
                ))
                metrics.record_model_event(request.model, "request")
                return out

        if is_stream:
            stream_gen = handle_streaming(
                out, request,
                model_context_window=ctx.effective_context_window or cfg.routing.model_context_window,
                classifier_model=cfg.classifier.model,
                classifier_api_key=cfg.classifier.api_key,
                classifier_base_url=cfg.classifier.base_url,
                strip_reasoning=cfg.policy.strip_reasoning,
            )

            # Analysis requests with refinements: wrap in quality loop
            if ctx.is_analysis and cfg.analysis.max_refinements > 0:
                stream_gen = analysis_quality_stream(
                    stream_gen, request, ctx, cfg,
                )

            # Wrap stream to capture post-stream metrics (tokens, quality, cost)
            stream_gen = tracked_stream(stream_gen, request, ctx, cfg)

            # Estimate input tokens from raw body (streaming doesn't report them)
            est_input_tokens = max(1, len(body) // 6)
            metrics.record(RequestLog(
                timestamp=datetime.now(timezone.utc).isoformat(),
                intent=ctx.intent,
                model_requested=original_model,
                model_used=request.model,
                provider=provider_used,
                input_tokens=est_input_tokens, output_tokens=0,
                latency_ms=elapsed_ms,
                is_fallback=provider_used != "primary",
                is_stream=True, is_analysis=ctx.is_analysis,
                phase=ctx.phase,
                cost_usd=0.0,  # updated post-stream via _tracked_stream
            ))
            metrics.record_model_event(request.model, "request")
            return StreamingResponse(
                stream_gen,
                media_type="text/event-stream",
            )

        anthropic_response = convert_litellm_to_anthropic(
            out, request, model_context_window=ctx.effective_context_window or cfg.routing.model_context_window,
            strip_reasoning=cfg.policy.strip_reasoning,
        )

        # ── Analysis quality refinement loop (non-streaming only) ──
        max_refinements = cfg.analysis.max_refinements if ctx.is_analysis else 0
        quality_threshold = cfg.analysis.quality_threshold
        if max_refinements > 0:
            score, issues = score_anthropic_response(anthropic_response, ctx.intent, is_analysis=True)
            ctx.quality_score = score
            ctx.quality_issues = issues

            for attempt in range(1, max_refinements + 1):
                if score >= quality_threshold:
                    break

                logger.info(
                    "[refinement] attempt=%d/%d score=%.2f threshold=%.2f issues=%s",
                    attempt, max_refinements, score, quality_threshold, issues,
                )
                ctx.refinement_attempt = attempt

                feedback = (
                    f"[quality-feedback] Your previous analysis scored {score:.0%}. "
                    f"Issues: {', '.join(issues)}. "
                    "Re-analyze with more depth. Read files you mentioned but didn't verify. "
                    "Provide function signatures, line counts, and concrete evidence."
                )

                resp_text = extract_response_text(anthropic_response)

                if not hasattr(request, "messages") or request.messages is None:
                    break

                from llm.schemas import Message
                request.messages.append(Message(role="assistant", content=resp_text[:4000]))
                request.messages.append(Message(role="user", content=feedback))

                _, out, provider_used = await run_messages(
                    request_obj=request, cfg=cfg, ctx=ctx,
                )
                anthropic_response = convert_litellm_to_anthropic(
                    out, request, model_context_window=ctx.effective_context_window or cfg.routing.model_context_window,
                )
                score, issues = score_anthropic_response(anthropic_response, ctx.intent, is_analysis=True)
                ctx.quality_score = score
                ctx.quality_issues = issues

            if ctx.refinement_attempt > 0:
                logger.info(
                    "[refinement] done: attempts=%d final_score=%.2f issues=%s",
                    ctx.refinement_attempt, ctx.quality_score, ctx.quality_issues,
                )

        input_tokens = getattr(anthropic_response, "usage", None)
        in_tok = input_tokens.input_tokens if input_tokens else 0
        out_tok = input_tokens.output_tokens if input_tokens else 0
        req_cost = cfg.model_costs.cost_usd(request.model, in_tok, out_tok)

        # Quality scoring for all non-streaming responses
        if ctx.quality_score == 1.0 and ctx.refinement_attempt == 0:
            q_score, q_issues = score_anthropic_response(
                anthropic_response, ctx.intent, is_analysis=ctx.is_analysis,
            )
            ctx.quality_score = q_score
            ctx.quality_issues = q_issues

        metrics.record(RequestLog(
            timestamp=datetime.now(timezone.utc).isoformat(),
            intent=ctx.intent,
            model_requested=original_model,
            model_used=request.model,
            provider=provider_used,
            input_tokens=in_tok, output_tokens=out_tok,
            latency_ms=elapsed_ms,
            is_fallback=provider_used != "primary",
            is_stream=False, is_analysis=ctx.is_analysis,
            phase=ctx.phase,
            refinement_attempts=ctx.refinement_attempt,
            quality_score=ctx.quality_score,
            cost_usd=req_cost,
        ))
        metrics.record_model_event(request.model, "request")
        has_tools = any(
            (c.get("type") if isinstance(c, dict) else getattr(c, "type", None)) == "tool_use"
            for c in anthropic_response.content
        )
        if has_tools:
            metrics.record_model_event(request.model, "tool_success")

        # Classifier validation: detect obvious misclassifications post-response
        tool_count = sum(
            1 for c in anthropic_response.content
            if (c.get("type") if isinstance(c, dict) else getattr(c, "type", None)) == "tool_use"
        )
        resp_text_len = len(extract_response_text(anthropic_response))
        if ctx.intent == "CHAT" and tool_count > 3:
            metrics.record_model_event("classifier", "validated_wrong_chat")
        elif ctx.intent == "BUILD" and resp_text_len > 1000 and tool_count == 0:
            metrics.record_model_event("classifier", "validated_wrong_build")

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
            metrics.increment_cache_hit()
            input_tokens = cached
        else:
            metrics.increment_cache_miss()
            try:
                input_tokens = token_counter(
                    model=target_model,
                    messages=litellm_messages
                )
            except Exception:
                # Fallback: approximate using bytes heuristic
                total_chars = sum(len(str(m.get("content", ""))) for m in litellm_messages)
                input_tokens = max(1, total_chars // 3)
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
            "small": {
                "model": cfg.routing.small_model,
                "provider": cfg.routing.small_route.provider if cfg.routing.small_route else cfg.routing.preferred_provider,
            },
            "big": {
                "model": cfg.routing.big_model,
                "provider": cfg.routing.preferred_provider,
            },
            "building": {
                "model": cfg.routing.building_model,
                "provider": cfg.routing.building_route.provider if cfg.routing.building_route else cfg.routing.preferred_provider,
            },
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
