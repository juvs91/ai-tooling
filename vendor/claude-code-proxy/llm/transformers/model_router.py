# llm/transformers/model_router.py
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

from llm.pipeline import Transformer, TransformContext
from router.model_mapper import map_claude_alias_to_target, _provider_prefix
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
    ) -> None:
        self._routing = routing_cfg
        self._creds = credentials_cfg
        self._analysis = analysis_cfg

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
            request.model = f"openai/{chosen}"
        else:
            current = request.model
            prefix = current.rsplit("/", 1)[0] if "/" in current else "openai"
            if ctx.phase == "EXPLORE" and self._routing.small_model != self._routing.big_model:
                route = self._routing.small_route
                if route:
                    # Cross-provider: use route's provider prefix + credentials
                    request.model = f"{route.provider}/{self._routing.small_model}"
                    ctx.route_override = route
                else:
                    request.model = f"{prefix}/{self._routing.small_model}"
            elif ctx.phase == "EXECUTE" and self._routing.building_model != self._routing.big_model:
                route = self._routing.building_route
                if route:
                    request.model = f"{route.provider}/{self._routing.building_model}"
                    ctx.route_override = route
                else:
                    request.model = f"{prefix}/{self._routing.building_model}"
            else:
                # PLAN phase: always force big_model using the configured provider prefix from env.
                # map_claude_alias_to_target maps haiku → small_model (wrong for PLAN).
                # prefix is derived from the (incorrect) mapped model, so use preferred_provider directly.
                pref = _provider_prefix(self._routing.preferred_provider)
                request.model = f"{pref}{self._routing.big_model}"

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
