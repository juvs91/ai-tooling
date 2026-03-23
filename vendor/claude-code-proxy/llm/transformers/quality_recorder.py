# llm/transformers/quality_recorder.py
"""QualityRecorderTransformer — post-response quality window updater for adaptive routing."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from llm.pipeline import Transformer, TransformContext
from utils.metrics import metrics as _metrics


class QualityRecorderTransformer(Transformer):
    """Post-response: records grounding+quality score per model in the rolling window.

    Runs in the RESPONSE pipeline after GroundingValidatorTransformer.
    Feeds the adaptive routing quality windows in metrics.

    Does NOT modify the response — side-effect only on metrics singleton.
    """

    @property
    def name(self) -> str:
        return "quality_recorder"

    async def transform(self, response: Any, ctx: TransformContext) -> None:
        if not ctx.is_analysis:
            return

        model = getattr(response, "model", "") or ""
        if not model:
            return

        _metrics.update_model_quality(
            model=model,
            quality_score=ctx.quality_score,
            grounding_score=ctx.grounding_score,
            intent=ctx.intent,
        )
        logger.debug(
            "[quality-recorder] model=%s quality=%.2f grounding=%.2f intent=%s",
            model, ctx.quality_score, ctx.grounding_score, ctx.intent,
        )
