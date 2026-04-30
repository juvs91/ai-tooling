# llm/transformers/provider_quirks.py
from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from config import ProviderQuirksConfig

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

    def __init__(
        self,
        stream_extra_body: Optional[dict] = None,
        litellm_thinking_params: Optional[dict] = None,
        analysis_thinking: Optional[dict] = None,
        quirks_cfg: Optional["ProviderQuirksConfig"] = None,
    ) -> None:
        self._extra = stream_extra_body
        self._litellm_thinking_params = litellm_thinking_params or {}
        self._analysis_thinking = analysis_thinking or {}
        self._quirks = quirks_cfg

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

        # Kimi K2: clamp high temperatures — quality collapses at temp > threshold in long sessions
        if "kimi" in model.lower():
            current_temp = ctx.litellm_request.get("temperature")
            max_temp = self._quirks.kimi_max_temp if self._quirks else 0.8
            clamp_temp = self._quirks.kimi_clamp_temp if self._quirks else 0.6
            if current_temp is not None and current_temp > max_temp:
                ctx.litellm_request["temperature"] = clamp_temp
                logger.info("[quirks] kimi-k2: temp_clamped %.1f (was %.1f)", clamp_temp, current_temp)

        # LiteLLM provider-specific thinking + generic fallback
        # Only applies during ANALYZING/READ/SYNTHESIZING phases
        model = str(getattr(request, "model", "") or "")
        if ctx.litellm_request and getattr(ctx, "analysis_phase", "") in ("ANALYZING", "READ", "SYNTHESIZING"):
            thinking_handled = False
            if self._litellm_thinking_params:
                if "deepseek" in model.lower():
                    # DeepSeek R1 uses "max_tokens" for thinking output
                    analysis_max = self._quirks.deepseek_analysis_max_tokens if self._quirks else 8000
                    current_max = ctx.litellm_request.get("max_tokens", 0)
                    if current_max < analysis_max:
                        ctx.litellm_request["max_tokens"] = analysis_max
                        logger.info("[quirks] deepseek: max_tokens bumped to %d (thinking output)", analysis_max)
                    thinking_handled = True
                elif "minimax" in model.lower():
                    minimax_params = self._litellm_thinking_params.get("minimax")
                    if minimax_params and minimax_params.get("thinking"):
                        ctx.litellm_request.setdefault("extra_body", {}).update(minimax_params["thinking"])
                        logger.info("[quirks] minimax: thinking injected %s", list(minimax_params.get("thinking", {}).keys()))
                    thinking_handled = True
                elif "kimi" in model.lower():
                    kimi_params = self._litellm_thinking_params.get("kimi")
                    if kimi_params and kimi_params.get("thinking"):
                        ctx.litellm_request.setdefault("extra_body", {}).update(kimi_params["thinking"])
                        logger.info("[quirks] kimi-k2: thinking injected %s", list(kimi_params.get("thinking", {}).keys()))
                    thinking_handled = True

            # Generic fallback: inject ANALYSIS_THINKING_PARAMS into extra_body for unknown
            # LiteLLM models that accept Anthropic-format thinking params
            if not thinking_handled and self._analysis_thinking:
                ctx.litellm_request.setdefault("extra_body", {}).update(self._analysis_thinking)
                logger.info("[quirks] analysis_thinking (generic fallback): %s", list(self._analysis_thinking.keys()))
