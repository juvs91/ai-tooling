# app/proxy/proxy.py
from __future__ import annotations
import asyncio
import os
import uuid
from threading import Lock
from typing import Any, Tuple

import logging

import httpx
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
from llm.passthrough import PassthroughClient, PassthroughError

logger = logging.getLogger(__name__)
from utils.metrics import metrics
from llm.converters import convert_anthropic_to_litellm
from llm.pipeline import Pipeline, TransformContext
from llm.transformers import (
    IntentClassifierTransformer,
    GuardrailTransformer,
    TokenCapTransformer,
    ToolAllowlistTransformer,
    ModelRouterTransformer,
    CompressionTransformer,
    ProviderQuirksTransformer,
    CredentialTransformer,
    IntentEnforcementTransformer,
    # ── AGNOSTIC RESPONSE TRANSFORMERS ────────────────────────────────────────
    ReasoningHandlingTransformer,
    UniversalToolExtractionTransformer,
    ModelFeedbackTransformer,
    QualityRefinementTransformer,
    StreamEventTransformer,
    # ──────────────────────────────────────────────────────────────────────────────────────
)

from config import ProxyConfig


# ── Pipeline builders ────────────────────────────────────────────────

def build_request_pipeline(cfg: ProxyConfig, models_differ: bool) -> Pipeline:
    """Phase 1: Transformers that operate on the Anthropic-format request."""
    return Pipeline([
        IntentClassifierTransformer(
            cfg.classifier, cfg.policy, models_differ,
            synth_reads_fallback=cfg.analysis.synthesize_reads_fallback,
        ),
        IntentEnforcementTransformer(enabled=True),  # Validate intent compliance
        GuardrailTransformer(cfg.policy.guard_system),
        TokenCapTransformer(cfg.policy, cfg.credentials.openai_base_url),
        ToolAllowlistTransformer(cfg.policy),
        ModelRouterTransformer(cfg.routing, cfg.credentials, cfg.analysis),
    ])


def build_litellm_pipeline(cfg: ProxyConfig) -> Pipeline:
    """Phase 2: Transformers that operate on the LiteLLM-format request."""
    return Pipeline([
        CompressionTransformer(cfg.compressor, cfg.routing),
        ProviderQuirksTransformer(cfg.stream_extra_body),
        CredentialTransformer(cfg.credentials, cfg.analysis),
    ])


def build_passthrough_pipeline(cfg: ProxyConfig) -> Pipeline:
    """Phase 2b: Request transformers for passthrough (Anthropic-compatible endpoints like Z.AI)."""
    return Pipeline([
        CompressionTransformer(cfg.compressor, cfg.routing),
    ])
    # Note: Response transformers run AFTER model returns, NOT here
    # See run_messages() passthrough path for response pipeline integration


def build_response_pipeline(cfg: ProxyConfig) -> Pipeline:
    """AGNOSTIC RESPONSE pipeline for universal tool extraction.

    Runs AFTER model returns response, processes ALL output types:
    - thinking, content, tools, mixed responses
    - Works for ALL models (no model-specific logic)
    - Extracts tools, cleans XML, provides AGNOSTIC feedback

    Key Design Decisions:
    1. NO model-specific if/elif blocks (AGNOSTIC)
    2. Processes model OUTPUT (not INPUT)
    3. Runs AFTER model endpoint, BEFORE client response
    4. Uses existing Transformer classes (already implemented and tested)
    """
    return Pipeline([
        ReasoningHandlingTransformer(cfg.analysis),
        UniversalToolExtractionTransformer(),
        ModelFeedbackTransformer(cfg),
        QualityRefinementTransformer(cfg),
        # StreamEventTransformer(enabled=False),  # Temporarily disabled due to Pydantic issues
    ])


async def _run_response_pipeline(response: Any, ctx: TransformContext, cfg: ProxyConfig) -> None:
    """Build and execute the response pipeline, propagating request context.

    Single canonical call site shared by LiteLLM non-stream (server.py) and
    passthrough non-stream (proxy.py) so the TransformContext construction
    and pipeline invocation are never duplicated.
    """
    response_ctx = TransformContext(
        intent=ctx.intent,
        is_analysis=ctx.is_analysis,
        phase=ctx.phase,
        analysis_phase=ctx.analysis_phase,
        tools=ctx.tools,
    )
    await build_response_pipeline(cfg).process(response, response_ctx)


_litellm_pipeline_cache: Pipeline | None = None
_passthrough_pipeline_cache: Pipeline | None = None
_litellm_pipeline_lock = Lock()


def _get_litellm_pipeline(cfg: ProxyConfig) -> Pipeline:
    """Return a cached Phase 2 pipeline (built once on first call, thread-safe)."""
    global _litellm_pipeline_cache
    if _litellm_pipeline_cache is None:
        with _litellm_pipeline_lock:
            if _litellm_pipeline_cache is None:  # double-check inside lock
                _litellm_pipeline_cache = build_litellm_pipeline(cfg)
    return _litellm_pipeline_cache


def _get_passthrough_pipeline(cfg: ProxyConfig) -> Pipeline:
    """Return a cached passthrough pipeline (built once on first call, thread-safe)."""
    global _passthrough_pipeline_cache
    if _passthrough_pipeline_cache is None:
        with _litellm_pipeline_lock:
            if _passthrough_pipeline_cache is None:  # double-check inside lock
                _passthrough_pipeline_cache = build_passthrough_pipeline(cfg)
    return _passthrough_pipeline_cache


# ── Execution layer (retry + fallback — unchanged) ──────────────────

async def _call_provider(request_obj: Any, litellm_request: dict) -> Tuple[bool, Any]:
    """Execute a single litellm call. For streaming, validates the first chunk."""
    # Timeout protects against provider hangs (e.g., MiniMax 92.6s spike)
    litellm_request.setdefault("timeout", 60)

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

    resp = await litellm.acompletion(**litellm_request)

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
            # Fix: was < max_retries, changed to < max_retries - 1 for correct retry count
            # With max_retries=5, range(5) gives attempts 0,1,2,3,4
            # We want to retry on attempts 0,1,2,3 but NOT on 4 (last attempt)
            if attempt < max_retries - 1 and _is_retryable_error(e):
                delay = base_delay * (2 ** attempt)
                print(f"[retry] Attempt {attempt + 1}/{max_retries} failed, retry in {delay}s: {type(e).__name__}: {str(e)[:200]}")
                metrics.total_retries += 1
                await asyncio.sleep(delay)
            else:
                if not _is_retryable_error(e):
                    print(f"[retry] Non-retryable error on attempt {attempt + 1}: {type(e).__name__}: {str(e)[:200]}")
                else:
                    print(f"[retry] Max retries ({max_retries}) exceeded, giving up")
                raise

    raise last_exception


# ── Passthrough helpers ───────────────────────────────────────────────

def _is_passthrough_compatible(model: str, cfg: ProxyConfig) -> bool:
    """Auto-detect if model targets an Anthropic endpoint that supports passthrough.

    Passthrough bypasses LiteLLM by sending Anthropic format directly via httpx.
    Only activates when: (1) not disabled, (2) custom anthropic base_url exists,
    and (3) model uses anthropic/ prefix or is a bare model name.
    """
    if cfg.passthrough_disabled:
        return False
    # Need a custom Anthropic endpoint (Z.AI, etc.) — actual Anthropic API
    # is handled natively by LiteLLM with no conversion overhead.
    if not cfg.credentials.anthropic_base_url:
        return False
    if not cfg.credentials.anthropic_api_key:
        return False
    # Check model prefix
    if "/" not in model:
        return True  # bare model → assume primary provider
    return model.split("/", 1)[0].lower() == "anthropic"


async def _empty_stream():
    """Yield nothing — used when passthrough stream is immediately exhausted."""
    return
    yield  # noqa: unreachable — makes this an async generator


def _build_passthrough_body(
    request: Any,
    model: str,
    ctx: TransformContext | None = None,
    analysis_thinking: dict | None = None,
) -> dict:
    """Convert MessagesRequest to Anthropic API body dict for passthrough.

    When ctx.analysis_phase == "ANALYZING" and analysis_thinking is provided,
    merges thinking params into the body (model-agnostic activation).
    """
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": getattr(request, "max_tokens", 4096),
        "messages": [
            m if isinstance(m, dict) else m.dict()
            for m in (request.messages or [])
        ],
    }
    if getattr(request, "system", None):
        system = request.system
        if isinstance(system, list):
            body["system"] = [
                s if isinstance(s, dict) else s.dict() for s in system
            ]
        else:
            body["system"] = system
    if getattr(request, "tools", None):
        body["tools"] = [
            t if isinstance(t, dict) else t.dict() for t in request.tools
        ]
    if getattr(request, "temperature", None) is not None:
        body["temperature"] = request.temperature
    # Inject thinking params for ANALYZING/READ phase only.
    # analysis_thinking comes from ANALYSIS_THINKING_PARAMS env var — it's specific to
    # the model configured there (e.g. GLM-4.7 via Anthropic passthrough). We don't
    # propagate CC's thinking param to other models since they may not support it.
    if ctx and ctx.analysis_phase in ("ANALYZING", "READ") and analysis_thinking:
        # Skip thinking for very large contexts — reasoning overhead causes timeouts
        thinking_cap = int(os.environ.get("THINKING_MAX_INPUT_CHARS", "0"))
        if thinking_cap > 0:
            body_chars = sum(len(str(m)) for m in body.get("messages", []))
            if body_chars > thinking_cap:
                logger.info("[passthrough] SKIP thinking: body_chars=%d > cap=%d", body_chars, thinking_cap)
                return body
        body.update(analysis_thinking)
        logger.info("[passthrough] injected thinking params: %s", list(analysis_thinking.keys()))
    return body


# ── Main entry point ─────────────────────────────────────────────────

async def run_messages(
    *,
    request_obj: Any,
    cfg: ProxyConfig,
    ctx: TransformContext,
) -> Tuple[bool, Any, str]:
    """
    Bridge + Phase 2 + Execution.

    1. Convert Anthropic → LiteLLM format
    2. Run litellm_pipeline (compression, quirks, credentials)
    3. Execute with retry + fallback chain

    Returns: (is_streaming, response_or_generator, provider_name)
    """
    # Store original tools in context for response transformers to access
    ctx.tools = getattr(request_obj, "tools", None)

    model = str(getattr(request_obj, "model", "") or "")
    model_ctx = ctx.effective_context_window or cfg.routing.model_context_window

    # ── Passthrough: auto-detect Anthropic-compatible endpoints ──
    if _is_passthrough_compatible(model, cfg):
        pt_model = model.split("/")[-1] if "/" in model else model
        try:
            is_stream = getattr(request_obj, "stream", False)
            has_thinking = (
                is_stream
                and ctx and ctx.analysis_phase in ("ANALYZING", "READ")
                and cfg.analysis.thinking_params
            )
            # Dynamic timeout: reasoning models need more time with large contexts
            timeout = cfg.passthrough_thinking_timeout if has_thinking else cfg.passthrough_timeout

            # Convert to LiteLLM format for compression transformer
            ctx.litellm_request = convert_anthropic_to_litellm(
                request_obj, model_context_window=model_ctx,
                max_output_tokens=cfg.routing.max_output_tokens,
                reasoning_max_tokens=cfg.routing.reasoning_max_tokens,
            )

            # Run passthrough pipeline (compression) before building body
            await _get_passthrough_pipeline(cfg).process(request_obj, ctx)

            pt = PassthroughClient(cfg.credentials.anthropic_base_url, cfg.credentials.anthropic_api_key, timeout=timeout)
            body = _build_passthrough_body(
                request_obj, pt_model,
                ctx=ctx,
                analysis_thinking=cfg.analysis.thinking_params if is_stream else None,
            )
            if is_stream:
                logger.info("[passthrough] streaming phase=%s model=%s analysis=%s timeout=%.0fs", ctx.phase, body.get("model"), ctx.analysis_phase, timeout)
                # Don't strip reasoning during analysis — the reasoning IS the value
                strip = cfg.policy.strip_reasoning and not ctx.is_analysis
                raw_stream = pt.stream_message(body, strip_reasoning=strip, response_model=model)
                # Eagerly fetch first chunk to detect connection/timeout errors
                # BEFORE returning StreamingResponse (enables litellm fallback)
                try:
                    first_chunk = await raw_stream.__anext__()
                except StopAsyncIteration:
                    return True, _empty_stream(), "passthrough"
                except Exception as stream_err:
                    logger.warning("[passthrough] stream failed on first chunk (timeout=%.0fs): %s", timeout, stream_err)
                    raise PassthroughError(str(stream_err)) from stream_err

                # Passthrough streaming: tool extraction handled by passthrough_xml_tool_extraction in server.py
                # Response transformers are designed for complete responses, not SSE event strings
                async def _prepend_stream():
                    yield first_chunk
                    async for chunk in raw_stream:
                        yield chunk

                return True, _prepend_stream(), "passthrough"
                # ──────────────────────────────────────────────────────────────────────────────
            else:
                # Non-streaming passthrough: use actual max_tokens to support
                # quality refinement loop in server.py. The original max_tokens=1
                # cap was a "preflight" optimization but broke quality scoring.
                body["max_tokens"] = getattr(request_obj, "max_tokens", 4096)
                logger.info("[passthrough] non-stream phase=%s model=%s max_tokens=%d",
                            ctx.phase, body.get("model"), body["max_tokens"])

                # Get response from model, then run response pipeline
                anthropic_response = await pt.create_message(body)
                await _run_response_pipeline(anthropic_response, ctx, cfg)
                return False, anthropic_response, "passthrough"
                # ──────────────────────────────────────────────────────────────────────────────
        except (httpx.HTTPError, PassthroughError) as e:
            logger.warning("[passthrough] FALLBACK to litellm: %s: %s", type(e).__name__, e)
            # Fall through to normal litellm pipeline
    elif cfg.credentials.anthropic_base_url and not cfg.passthrough_disabled:
        logger.info("[passthrough] SKIP: model=%s not anthropic-compatible", model)

    # Bridge: Anthropic → LiteLLM format
    ctx.litellm_request = convert_anthropic_to_litellm(
        request_obj, model_context_window=model_ctx,
        max_output_tokens=cfg.routing.max_output_tokens,
        reasoning_max_tokens=cfg.routing.reasoning_max_tokens,
    )

    # Phase 2: LiteLLM transformers (compression, provider quirks, credentials)
    await _get_litellm_pipeline(cfg).process(request_obj, ctx)

    # Execution: primary provider
    if not cfg.fallback_providers:
        is_stream, out = await _call_provider_with_retry(
            request_obj, ctx.litellm_request,
            max_retries=cfg.max_retries, base_delay=cfg.retry_base_delay,
        )
        return is_stream, out, "primary"

    # --- With fallback chain: retry primary, then fallbacks ---
    primary_error_msg = ""
    try:
        is_stream, out = await _call_provider_with_retry(
            request_obj, ctx.litellm_request,
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
            request_obj.model = provider.get_litellm_model(ctx.intent)
            fb_ctx = getattr(provider, "context_window", 0) or model_ctx
            fb_request = convert_anthropic_to_litellm(
                request_obj, model_context_window=fb_ctx,
                max_output_tokens=cfg.routing.max_output_tokens,
                reasoning_max_tokens=cfg.routing.reasoning_max_tokens,
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
