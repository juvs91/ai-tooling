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
    """Apply provider-specific request parameters (e.g. Z.AI tool_stream, LiteLLM thinking)."""

    @property
    def name(self) -> str:
        return "provider_quirks"

    def __init__(self, stream_extra_body: Optional[dict] = None, litellm_thinking_params: Optional[dict] = None) -> None:
        self._extra = stream_extra_body
        self._litellm_thinking_params = litellm_thinking_params or {}

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
            # NOTE: Only clamp if temperature is already present — if converters.py stripped it
            # (NO_TOOLS_MODELS path for cloud deepseek-reasoner which rejects temperature),
            # re-injecting would cause an API error.
            current_temp = ctx.litellm_request.get("temperature")
            if current_temp is not None and current_temp < 0.5:
                ctx.litellm_request["temperature"] = 0.6
                logger.info("[quirks] Overrode temperature %.1f → 0.6 for %s (R1 repetition prevention)", current_temp, model)

        # LiteLLM provider-specific thinking support (NEW)
        # Injects provider-specific thinking params for DeepSeek, MiniMax, etc.
        # Only applies during ANALYZING/READ/SYNTHESIZING phases
        model = str(getattr(request, "model", "") or "")
        if ctx.litellm_request and getattr(ctx, "analysis_phase", "") in ("ANALYZING", "READ", "SYNTHESIZING") and self._litellm_thinking_params:
            # Provider-specific thinking injection
            if "deepseek" in model.lower():
                # DeepSeek R1 uses "max_tokens" for thinking output
                if ctx.litellm_request.get("max_tokens"):
                    # DeepSeek R1 can handle larger max_tokens for thinking
                    # Default max_tokens is usually around 4K, but for R1 we want more
                    current_max = ctx.litellm_request.get("max_tokens", 0)
                    if current_max < 8000:
                        ctx.litellm_request["max_tokens"] = 8000
                        logger.info("[quirks] Injected DeepSeek R1 max_tokens: 8000 (for thinking output)")
            elif "minimax" in model.lower():
                # MiniMax thinking params (if supported)
                minimax_params = self._litellm_thinking_params.get("minimax")
                if minimax_params and minimax_params.get("thinking"):
                    ctx.litellm_request.setdefault("extra_body", {}).update(minimax_params["thinking"])
                    logger.info("[quirks] Injected MiniMax thinking params: %s", list(minimax_params.get("thinking", {}).keys()))
            # Add other providers as needed
