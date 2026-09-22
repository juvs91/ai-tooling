# app/proxy/proxy.py
from __future__ import annotations
import asyncio
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
from llm.passthrough import PassthroughClient, PassthroughError, try_structural_correction, build_passthrough_body
from llm.compressor import _track_grounding_hop
from llm.transformers import GroundingValidatorTransformer
from llm.state_assertion_rules import build_active_rules

logger = logging.getLogger(__name__)

# Build state-assertion rules once at import (ADR-0038+); reused per request.
_STATE_ASSERTION_RULES = build_active_rules()
from utils.metrics import metrics
from llm.converters import convert_anthropic_to_litellm
from llm.pipeline import Pipeline, TransformContext
from llm.transformers.plan_mode_enforcement import PlanModeEnforcementTransformer
from llm.transformers.thinking_signature_normalizer import ThinkingSignatureNormalizer
from llm.transformers import (
    IntentClassifierTransformer,
    GuardrailTransformer,
    TokenCapTransformer,
    ToolAllowlistTransformer,
    AdaptiveContextTransformer,
    ModelRouterTransformer,
    CompressionTransformer,
    ProviderQuirksTransformer,
    CredentialTransformer,
    IntentEnforcementTransformer,
    DeferredToolsTransformer,
    FragileModelPlanToolsTransformer,
    StateAssertionRequestTransformer,
    # ── AGNOSTIC RESPONSE TRANSFORMERS ────────────────────────────────────────
    ReasoningHandlingTransformer,
    UniversalToolExtractionTransformer,
    GroundingValidatorTransformer,
    StateAssertionResponseTransformer,
    ModelFeedbackTransformer,
    StreamEventTransformer,
    QualityRecorderTransformer,
    ToolCallValidatorTransformer,
    PlanModeGuardTransformer,
    # ──────────────────────────────────────────────────────────────────────────────────────
)

from config import ProxyConfig


# ── Per-provider concurrency semaphores ──────────────────────────────
# Keyed by provider base URL. Created lazily on first passthrough call.
# asyncio.Semaphore is safe to create lazily in async context (single-threaded event loop).
_provider_semaphores: dict[str, asyncio.Semaphore] = {}


def _get_provider_semaphore(base_url: str, max_concurrent: int) -> asyncio.Semaphore:
    if base_url not in _provider_semaphores:
        _provider_semaphores[base_url] = asyncio.Semaphore(max_concurrent)
    return _provider_semaphores[base_url]


# ── Pipeline builders ────────────────────────────────────────────────

def build_request_pipeline(cfg: ProxyConfig, models_differ: bool) -> Pipeline:
    """Phase 1: Transformers that operate on the Anthropic-format request."""
    return Pipeline([
        IntentClassifierTransformer(
            cfg.classifier, cfg.policy, models_differ,
            synth_reads_fallback=cfg.analysis.synthesize_reads_fallback,
        ),
        StateAssertionRequestTransformer(_STATE_ASSERTION_RULES),   # ADR-0036 — reads ctx.plan_mode_active/intent set above
        PlanModeEnforcementTransformer(),
        IntentEnforcementTransformer(enabled=True),  # Validate intent compliance
        GuardrailTransformer(cfg.policy.guard_system),
        DeferredToolsTransformer(),               # Inject <available-deferred-tools> into request.tools
        TokenCapTransformer(cfg.policy, cfg.credentials.openai_base_url),
        ToolAllowlistTransformer(cfg.policy),
        AdaptiveContextTransformer(cfg),
        ModelRouterTransformer(cfg.routing, cfg.credentials, cfg.analysis, cfg.adaptive),
        # ADR-0037 correction: DeferredToolsTransformer's fragile-model check (above)
        # runs BEFORE routing, so request.model is still the client-sent alias
        # (e.g. "claude-sonnet-5"), not the routed target (e.g. "anthropic/kimi-k2") —
        # confirmed via live fire test that this made ADR-0037 a no-op for the
        # common alias-routed case. This transformer re-applies the same
        # guarantee using the FINAL, correctly-routed request.model.
        FragileModelPlanToolsTransformer(),
    ])


def build_litellm_pipeline(cfg: ProxyConfig) -> Pipeline:
    """Phase 2: Transformers that operate on the LiteLLM-format request."""
    return Pipeline([
        CompressionTransformer(cfg.compressor, cfg.routing),
        ProviderQuirksTransformer(
            cfg.stream_extra_body,
            cfg.litellm_thinking_params,
            analysis_thinking=cfg.analysis.thinking_params,
            quirks_cfg=cfg.quirks,
        ),
        CredentialTransformer(cfg.credentials, cfg.analysis),
    ])


def build_passthrough_pipeline(cfg: ProxyConfig) -> Pipeline:
    """Phase 2b: Request transformers for passthrough (Anthropic-compatible endpoints like Z.AI)."""
    return Pipeline([
        CompressionTransformer(cfg.compressor, cfg.routing),
        ProviderQuirksTransformer(cfg.stream_extra_body, cfg.litellm_thinking_params, quirks_cfg=cfg.quirks),
    ])
    # Note: Response transformers run AFTER model returns, NOT here
    # See run_messages() passthrough path for response pipeline integration


def build_response_pipeline(cfg: ProxyConfig) -> Pipeline:
    """Agnostic response pipeline: reasoning, tool extraction, grounding, feedback."""
    return Pipeline([
        ThinkingSignatureNormalizer(),
        ReasoningHandlingTransformer(cfg.analysis),
        UniversalToolExtractionTransformer(),
        ToolCallValidatorTransformer(),
        PlanModeGuardTransformer(),
        GroundingValidatorTransformer(enabled=cfg.policy.grounding_validation_enabled if hasattr(cfg, "policy") and hasattr(cfg.policy, "grounding_validation_enabled") else True),
        StateAssertionResponseTransformer(_STATE_ASSERTION_RULES),
        ModelFeedbackTransformer(cfg),
        QualityRecorderTransformer(),
    ])


async def _run_response_pipeline(response: Any, ctx: TransformContext, cfg: ProxyConfig) -> None:
    """Shared response pipeline for LiteLLM and passthrough non-stream paths."""
    response_ctx = TransformContext(
        intent=ctx.intent,
        is_analysis=ctx.is_analysis,
        phase=ctx.phase,
        analysis_phase=ctx.analysis_phase,
        tools=ctx.tools,
        plan_mode_active=ctx.plan_mode_active,
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


# Execution layer (retry + fallback)

async def _call_provider(request_obj: Any, litellm_request: dict) -> Tuple[bool, Any]:
    """Execute a single litellm call. For streaming, validates the first chunk."""
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


# Passthrough helpers

def _get_passthrough_credentials(
    cfg: ProxyConfig,
    ctx: TransformContext,
) -> tuple[Optional[str], Optional[str]]:
    """Return (base_url, api_key) to use for Anthropic-compatible passthrough.

    If ctx.route_override has provider == 'anthropic', its api_key/base_url take
    precedence. This allows SMALL/BUILDING routes to use their own API keys even
    when all models are on the same Anthropic-compatible provider (e.g., Kimi).
    """
    route = getattr(ctx, "route_override", None)
    if route and route.provider.lower() == "anthropic":
        # Route override wins for anthropic passthrough.
        base_url = route.base_url or cfg.credentials.anthropic_base_url
        api_key = route.api_key
        return base_url, api_key
    return cfg.credentials.anthropic_base_url, cfg.credentials.anthropic_api_key


def _is_passthrough_compatible(model: str, cfg: ProxyConfig, ctx: TransformContext) -> bool:
    """Auto-detect if model targets an Anthropic endpoint that supports passthrough."""
    if cfg.passthrough_disabled:
        return False
    base_url, api_key = _get_passthrough_credentials(cfg, ctx)
    if not base_url:
        return False
    if not api_key:
        return False
    if "/" not in model:
        # Bare model name: require explicit prefix by default to avoid accidental
        # passthrough for DeepSeek/GLM when ANTHROPIC_BASE_URL is set.
        if cfg.passthrough_require_prefix:
            return False
        return True
    return model.split("/", 1)[0].lower() == "anthropic"


async def _empty_stream():
    """Yield nothing — used when passthrough stream is immediately exhausted."""
    return
    yield  # noqa: unreachable — makes this an async generator


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
    # original_model is the CC-facing model name (e.g. "claude-opus-4-6") set by
    # ModelRouterTransformer before it rewrites request.model to the provider model.
    # Used as response_model so CC activates model-gated UI (Plan panel, etc.).
    original_model = getattr(request_obj, "original_model", None) or model
    model_ctx = ctx.effective_context_window or cfg.routing.model_context_window

    # ── Passthrough: auto-detect Anthropic-compatible endpoints ──
    if _is_passthrough_compatible(model, cfg, ctx):
        pt_base_url, pt_api_key = _get_passthrough_credentials(cfg, ctx)
        pt_model = model.split("/")[-1] if "/" in model else model
        try:
            is_stream = getattr(request_obj, "stream", False)
            has_thinking = (
                is_stream
                and ctx and ctx.analysis_phase in ("ANALYZING", "READ", "SYNTHESIZING")
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

            pt = PassthroughClient(
                pt_base_url,
                pt_api_key,
                timeout=timeout,
                endpoint_path=cfg.credentials.anthropic_endpoint_path,
            )
            body = build_passthrough_body(
                request_obj, pt_model,
                analysis_phase=ctx.analysis_phase if ctx else None,
                analysis_thinking=cfg.analysis.thinking_params,
                passthrough_thinking=cfg.passthrough_thinking_params,
            )
            if is_stream:
                logger.info("[passthrough] streaming phase=%s model=%s analysis=%s timeout=%.0fs", ctx.phase, body.get("model"), ctx.analysis_phase, timeout)
                # Don't strip reasoning during analysis — the reasoning IS the value
                strip = cfg.policy.strip_reasoning and not ctx.is_analysis
                raw_stream = pt.stream_message(body, strip_reasoning=strip, response_model=original_model)
                # Eagerly fetch first chunk to detect connection/timeout errors
                # BEFORE returning StreamingResponse (enables litellm fallback)
                try:
                    first_chunk = await raw_stream.__anext__()
                except StopAsyncIteration:
                    return True, _empty_stream(), "passthrough"
                except Exception as stream_err:
                    logger.warning("[passthrough] stream failed on first chunk (timeout=%.0fs): %s", timeout, stream_err)
                    raise PassthroughError(str(stream_err)) from stream_err

                # ──────────────────────────────────────────────────────────────────────────────
                # NOTE: Response Pipeline NOT Called for Streaming
                # Response pipeline is skipped for streaming: transformers expect
                # complete response objects, not SSE strings. Grounding validation runs
                # asynchronously via tracked_stream() in server.py.
                async def _prepend_stream():
                    yield first_chunk
                    async for chunk in raw_stream:
                        yield chunk

                return True, _prepend_stream(), "passthrough"
            else:
                # Non-streaming: use actual max_tokens to support quality refinement loop.
                body["max_tokens"] = getattr(request_obj, "max_tokens", 4096)
                logger.info("[passthrough] non-stream phase=%s model=%s max_tokens=%d",
                            ctx.phase, body.get("model"), body["max_tokens"])

                # Call provider, run response pipeline, then structural correction if needed.
                sem = _get_provider_semaphore(
                    pt_base_url or "",
                    cfg.max_concurrent_per_provider,
                )
                async with sem:
                    anthropic_response = await pt.create_message(body, response_model=original_model)
                await _run_response_pipeline(anthropic_response, ctx, cfg)

                if (
                    any(q.startswith("structural:") for q in ctx.quality_issues)
                    and ctx.refinement_attempt == 0
                ):
                    ctx.refinement_attempt = 1
                    corrected = await try_structural_correction(
                        pt, body, anthropic_response, original_model
                    )
                    if corrected:
                        ctx.quality_issues = [
                            q for q in ctx.quality_issues if not q.startswith("structural:")
                        ]
                        await _run_response_pipeline(corrected, ctx, cfg)
                        anthropic_response = corrected
                        logger.info("[proxy] structural correction accepted")
                    else:
                        logger.warning("[proxy] structural correction failed — using auto-patch result")

                return False, anthropic_response, "passthrough"
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
            # Normalize model name in non-streaming response so CC's VSCode
            # extension receives the original claude-* model name and activates
            # model-gated UI like the Plan panel (same as passthrough does via
            # response_model= param).
            if not is_stream and original_model:
                try:
                    if isinstance(out, dict):
                        out["model"] = original_model
                    elif hasattr(out, "model"):
                        out.model = original_model
                except Exception:
                    pass
            return is_stream, out, provider.name
        except Exception as e:
            print(f"[fallback] {provider.name} failed: {e}")
            errors.append(f"{provider.name}: {e}")
            continue

    # Restore original model for error reporting
    request_obj.model = original_model
    raise Exception(f"All providers failed: {'; '.join(errors)}")
