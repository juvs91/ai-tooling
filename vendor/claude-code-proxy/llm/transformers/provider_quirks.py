# llm/transformers/provider_quirks.py
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

from llm.pipeline import Transformer, TransformContext


def _needs_reasoning_field(model: str) -> bool:
    """Models with 'reasoner' in name require reasoning_content on assistant history."""
    bare = model.split("/")[-1] if "/" in model else model
    return "reasoner" in bare.lower()


class ProviderQuirksTransformer(Transformer):
    """Apply provider-specific request parameters (e.g. Z.AI tool_stream)."""

    @property
    def name(self) -> str:
        return "provider_quirks"

    def __init__(self, stream_extra_body: Optional[dict]) -> None:
        self._extra = stream_extra_body

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        if (
            self._extra
            and ctx.litellm_request.get("stream")
            and ctx.litellm_request.get("tools")
        ):
            ctx.litellm_request.setdefault("extra_body", {}).update(self._extra)
            logger.info("[tools] Applied STREAM_EXTRA_BODY: %s", list(self._extra.keys()))

        # Reasoning models require reasoning_content on all assistant messages
        model = str(getattr(request, "model", "") or "")
        if _needs_reasoning_field(model):
            injected = 0
            messages = ctx.litellm_request.get("messages", [])
            for i, msg in enumerate(messages):
                if msg.get("role") == "assistant" and "reasoning_content" not in msg:
                    messages[i] = {**msg, "reasoning_content": ""}  # copy, don't mutate original
                    injected += 1
            if injected:
                logger.info("[quirks] Injected reasoning_content on %d assistant messages for %s", injected, model)

            # DeepSeek R1 needs temperature 0.5-0.7 to prevent endless repetition
            # (documented in DeepSeek R1 paper, confirmed via 4-hour loop incident)
            current_temp = ctx.litellm_request.get("temperature", 0)
            if current_temp is not None and current_temp < 0.5:
                ctx.litellm_request["temperature"] = 0.6
                logger.info("[quirks] Overrode temperature %.1f → 0.6 for %s (R1 repetition prevention)", current_temp, model)
