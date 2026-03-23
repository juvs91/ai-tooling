# llm/transformers/adaptive_context.py
"""AdaptiveContextTransformer — pre-routing quality history hydration for adaptive routing."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from llm.pipeline import Transformer, TransformContext
from utils.metrics import metrics as _metrics


class AdaptiveContextTransformer(Transformer):
    """Pre-routing: loads quality history for configured models into ctx.

    Runs in the REQUEST pipeline before ModelRouterTransformer.
    Populates ctx.model_quality_history so the router can make
    quality-informed routing decisions.

    Does NOT modify the request — only populates ctx.
    """

    @property
    def name(self) -> str:
        return "adaptive_context"

    def __init__(self, cfg: Any) -> None:
        """cfg: ProxyConfig (full config for routing + adaptive settings)."""
        self._cfg = cfg

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        if not self._cfg.adaptive.enabled:
            return

        routing = self._cfg.routing
        models_to_check: set[str] = {
            routing.small_model,
            routing.big_model,
            routing.building_model,
        }
        if routing.small_route and getattr(routing.small_route, "provider", None):
            models_to_check.add(routing.small_model)
        if routing.building_route and getattr(routing.building_route, "provider", None):
            models_to_check.add(routing.building_model)

        ctx.model_quality_history = {
            model: _metrics.get_model_quality_stats(model)
            for model in models_to_check
        }
        ctx.adaptive_routing_enabled = True

        logger.debug(
            "[adaptive-context] loaded quality history for %d models: %s",
            len(ctx.model_quality_history),
            {k: v.get("sample_size", 0) for k, v in ctx.model_quality_history.items()},
        )
