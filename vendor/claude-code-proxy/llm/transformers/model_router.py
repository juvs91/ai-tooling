# llm/transformers/model_router.py
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

from llm.pipeline import Transformer, TransformContext
from router.model_mapper import map_claude_alias_to_target, build_model_name, strip_provider_prefix
from router.llm_router import choose_local_model
from config import ModelRouting, ProviderCredentials, AnalysisConfig


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


class ModelRouterTransformer(Transformer):
    """Map Claude aliases to target models, apply intent-based routing."""

    @property
    def name(self) -> str:
        return "model_router"

    def __init__(
        self,
        routing_cfg: ModelRouting,
        credentials_cfg: ProviderCredentials,
        analysis_cfg: AnalysisConfig | None = None,
        adaptive_cfg: Any = None,
    ) -> None:
        self._routing = routing_cfg
        self._creds = credentials_cfg
        self._analysis = analysis_cfg
        self._adaptive = adaptive_cfg

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        # Preserve original model for logging (MUST happen before any mutation)
        if not getattr(request, "original_model", None):
            setattr(request, "original_model", getattr(request, "model", None))

        # Alias mapping: claude-3-5-sonnet → openai/glm-4.7
        request.model = map_claude_alias_to_target(
            getattr(request, "model", ""),
            preferred_provider=self._routing.preferred_provider,
            big_model=self._routing.big_model,
            small_model=self._routing.small_model,
        )

        # Phase-aware analysis routing (replaces unconditional ANALYSIS UPGRADE)
        if ctx.analysis_phase == "SYNTHESIZING" and self._analysis and self._analysis.model:
            # Only use expensive reasoning model for final synthesis
            request.model = self._analysis.model
            if self._analysis.max_tokens:
                request.max_tokens = self._analysis.max_tokens
            if self._analysis.context_window > 0:
                ctx.effective_context_window = self._analysis.context_window
            logger.info(
                "[route] ANALYSIS SYNTHESIZE: model=%s max_tokens=%s ctx_window=%s",
                self._analysis.model, self._analysis.max_tokens,
                self._analysis.context_window or "global",
            )
        # Intent-based routing (takes priority over analysis_phase for EXPLORE/EXECUTE)
        elif is_ollama_base(self._creds.openai_base_url):
            chosen = choose_local_model(
                messages=getattr(request, "messages", []) or [],
                max_out=int(getattr(request, "max_tokens", 0) or 0),
                approx_tokens=ctx.approx_tokens,
                system_chars=system_chars(getattr(request, "system", None)),
                tools_count=len(getattr(request, "tools", None) or []),
                small_model=self._routing.small_model,
                big_model=self._routing.big_model,
                building_model=self._routing.building_model,
                intent=ctx.intent,
            )
            request.model = build_model_name("openai", chosen)
        else:
            current = request.model
            prefix = current.rsplit("/", 1)[0] if "/" in current else "openai"
            if ctx.phase == "EXPLORE" and self._routing.small_model != self._routing.big_model:
                route = self._routing.small_route
                if route:
                    # Cross-provider: use route's provider prefix + credentials
                    request.model = build_model_name(route.provider, self._routing.small_model)
                    ctx.route_override = route
                else:
                    request.model = build_model_name(prefix, self._routing.small_model)
            elif ctx.phase == "EXECUTE" and self._routing.building_model != self._routing.big_model:
                tools_in = len(getattr(request, "tools", []) or [])
                if tools_in == 0:
                    # No tool definitions: building model (MiniMax-M2.5) can't generate
                    # tool_use responses without tool definitions → returns 0-token end_turn.
                    # Wrap-up turns (CC asking model to conclude after bash execution) and
                    # other tools_in=0 EXECUTE requests must go to big_model for a text response.
                    # No route_override → uses primary provider credentials (big_model is primary).
                    request.model = build_model_name(self._routing.preferred_provider, self._routing.big_model)
                    logger.info(
                        "[route] EXECUTE tools_in=0: building_model bypassed → big_model=%s",
                        request.model,
                    )
                else:
                    route = self._routing.building_route
                    if route:
                        request.model = build_model_name(route.provider, self._routing.building_model)
                        ctx.route_override = route
                    else:
                        request.model = build_model_name(prefix, self._routing.building_model)
            else:
                # PLAN phase: always force big_model using the configured provider prefix from env.
                # map_claude_alias_to_target maps haiku → small_model (wrong for PLAN).
                # prefix is derived from the (incorrect) mapped model, so use preferred_provider directly.
                request.model = build_model_name(self._routing.preferred_provider, self._routing.big_model)

        # PLAN intent: enable deep reasoning for planning
        if ctx.intent == "PLAN" and self._routing.reasoning_max_tokens > 0:
            request.max_tokens = self._routing.reasoning_max_tokens
            logger.info(
                "[route] PLAN reasoning: max_tokens=%d (reasoning_max_tokens)",
                self._routing.reasoning_max_tokens,
            )

        # Resolve effective context window for downstream transformers
        # Skip if already set (e.g. by analysis upgrade above)
        if ctx.route_override and ctx.route_override.context_window > 0:
            ctx.effective_context_window = ctx.route_override.context_window
        elif not ctx.effective_context_window:
            ctx.effective_context_window = self._routing.model_context_window

        # P3: Confidence-aware routing — low confidence → upgrade to big_model
        if (getattr(ctx, "intent_confidence", 1.0) < self._routing.low_confidence_threshold
                and ctx.intent in ("READ", "CHAT", "VERIFY")
                and self._routing.big_model != self._routing.small_model
                and not is_ollama_base(self._creds.openai_base_url)):
            request.model = build_model_name(self._routing.preferred_provider, self._routing.big_model)
            logger.info(
                "[router] Low confidence (%.2f) on %s → upgrading to big_model",
                ctx.intent_confidence, ctx.intent,
            )
        elif (getattr(ctx, "secondary_intent", "") == "BUILD"
                and ctx.intent == "READ"
                and getattr(ctx, "intent_confidence", 1.0) < 0.75
                and not is_ollama_base(self._creds.openai_base_url)):
            ctx.phase = "PLAN"
            request.model = build_model_name(self._routing.preferred_provider, self._routing.big_model)
            logger.info(
                "[router] Multi-intent READ+BUILD (conf=%.2f) → PLAN routing",
                ctx.intent_confidence,
            )

        # P2: Adaptive routing — upgrade if model quality history is poor
        if (self._adaptive is not None
                and self._adaptive.enabled
                and getattr(ctx, "adaptive_routing_enabled", False)
                and ctx.model_quality_history
                and not is_ollama_base(self._creds.openai_base_url)):
            adjusted = self._adaptive_adjust(request.model, ctx)
            if adjusted != request.model:
                logger.warning(
                    "[adaptive-routing] %s → %s | reason: %s",
                    request.model, adjusted, getattr(ctx, "adaptive_routing_reason", ""),
                )
                request.model = adjusted
                ctx.adaptive_routing_used = True

        logger.info(
            "[route] approx_tokens=%d intent=%s phase=%s provider=%s is_ollama=%s "
            "analysis_phase=%s model_in=%s model_out=%s tools_in=%d dropped=%s "
            "route_override=%s effective_ctx=%d",
            ctx.approx_tokens, ctx.intent, ctx.phase,
            self._routing.preferred_provider,
            is_ollama_base(self._creds.openai_base_url), ctx.analysis_phase,
            getattr(request, "original_model", "n/a"), request.model,
            len(getattr(request, "tools", []) or []), ctx.dropped_tools,
            bool(ctx.route_override), ctx.effective_context_window,
        )

    def _adaptive_adjust(self, chosen_model: str, ctx: TransformContext) -> str:
        """Adjust model if quality history is below threshold. Returns adjusted model name."""
        if self._adaptive is None:
            return chosen_model
        model_key = chosen_model.rsplit("/", 1)[-1]
        stats = ctx.model_quality_history.get(model_key, {})
        avg = stats.get("avg_quality")
        sample = stats.get("sample_size", 0)
        trend = stats.get("trend", "stable")

        if avg is None or sample < self._adaptive.min_sample_size:
            return chosen_model

        threshold = self._adaptive.quality_fallback_threshold
        badly_below = avg < threshold - 0.15
        below_degrading = avg < threshold and trend == "degrading"

        if not (badly_below or below_degrading):
            return chosen_model

        big = strip_provider_prefix(self._routing.big_model)
        big_stats = ctx.model_quality_history.get(big, {})
        big_avg = big_stats.get("avg_quality")  # None = no data = assume good

        # Upgrade to big_model if small is underperforming on analysis
        if (model_key == self._routing.small_model
                and ctx.is_analysis
                and (big_avg is None or big_avg >= threshold)):
            ctx.adaptive_routing_reason = f"small_model avg={avg:.2f} trend={trend}"
            return build_model_name(self._routing.preferred_provider, big)

        # Upgrade to big_model if building_model is underperforming on BUILD
        if (model_key == self._routing.building_model
                and ctx.intent == "BUILD"
                and (big_avg is None
                     or big_avg > avg + self._adaptive.min_quality_advantage)):
            ctx.adaptive_routing_reason = f"building_model avg={avg:.2f} trend={trend}"
            return build_model_name(self._routing.preferred_provider, big)

        return chosen_model
