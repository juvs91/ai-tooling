# llm/transformers/compression.py
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

from llm.pipeline import Transformer, TransformContext
from llm.compressor import compress_messages_if_needed, estimate_tools_tokens
from llm.compressor import get_or_create_session, update_session
from llm.tool_prompting import is_no_tools_model
from config import CompressorConfig, ModelRouting


class CompressionTransformer(Transformer):
    """Compress context if approaching window limit, recalculate max tokens."""

    @property
    def name(self) -> str:
        return "compression"

    def __init__(self, compressor_cfg: CompressorConfig, routing_cfg: ModelRouting) -> None:
        self._comp = compressor_cfg
        self._routing = routing_cfg

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        # Detect CC conversation compaction request (model-agnostic detection by content).
        # Official CC compaction prompt always contains "partial transcript".
        # If detected: (1) skip proxy compression — model needs full context to summarize,
        # (2) force big_model — needs large context window + Anthropic format for <summary></summary>.
        _litellm_msgs = ctx.litellm_request.get("messages", [])
        _is_compact = False
        for _m in _litellm_msgs:
            if _m.get("role") == "user":
                _content = _m.get("content", "")
                if isinstance(_content, list):
                    _content = " ".join(
                        c.get("text", "") if isinstance(c, dict) else str(c) for c in _content
                    )
                if "partial transcript" in str(_content).lower():
                    _is_compact = True
                    break

        if _is_compact:
            from router.model_mapper import _provider_prefix
            _pref = _provider_prefix(self._routing.preferred_provider)
            _target = f"{_pref}{self._routing.big_model}"
            logger.info(
                "[compress] COMPACT: CC compaction request — skip compression, force model=%s",
                _target,
            )
            ctx.litellm_request["model"] = _target
            ctx.was_compressed = False
            return

        model = str(getattr(request, "model", "") or "")
        model_ctx = ctx.effective_context_window or self._routing.model_context_window

        if model_ctx <= 0 or not self._comp.model or not self._comp.api_key:
            return

        tools_overhead = estimate_tools_tokens(ctx.litellm_request.get("tools"))

        ctx.litellm_request["messages"], ctx.was_compressed = await compress_messages_if_needed(
            messages=ctx.litellm_request["messages"],
            session_id=ctx.session_id,
            cfg=self._comp,
            model_context_window=model_ctx,
            compressor_model=self._comp.model,
            compressor_api_key=self._comp.api_key,
            compressor_base_url=self._comp.base_url,
            tools_overhead_tokens=tools_overhead,
            target_model=model,
        )

        # Recalculate max_completion_tokens after compression.
        # Previously restricted to openai/ prefix, but deepseek/ and other
        # providers also need recalculation (bug: max_tokens stayed at 1024
        # after compressing 106K→10K for DeepSeek-R1's 64K window).
        if ctx.was_compressed:
            no_tools = is_no_tools_model(model)
            if no_tools and self._routing.reasoning_max_tokens > 0:
                # Reasoning models: reapply reasoning cap after compression
                new_max = min(request.max_tokens, self._routing.reasoning_max_tokens)
                old_max = ctx.litellm_request.get("max_completion_tokens", new_max)
                if new_max != old_max:
                    logger.info(
                        "[compress] Recapped max_tokens: %d → %d (reasoning cap)",
                        old_max, new_max,
                    )
                ctx.litellm_request["max_completion_tokens"] = new_max
            elif not no_tools:
                provider_max = self._routing.max_output_tokens
                input_est = sum(
                    len(str(m.get("content", ""))) for m in ctx.litellm_request["messages"]
                ) // 4
                tools_est = sum(
                    len(json.dumps(t)) // 4
                    for t in (ctx.litellm_request.get("tools") or [])
                )
                remaining = model_ctx - input_est - tools_est
                safe = int(remaining * 0.85)
                new_cap = max(1024, min(safe, provider_max))
                new_max = min(request.max_tokens, new_cap)
                old_max = ctx.litellm_request.get("max_completion_tokens", new_max)
                if new_max != old_max:
                    logger.info(
                        "[compress] Recapped max_tokens: %d → %d "
                        "(post-compression input~%d tools~%d remaining~%d)",
                        old_max, new_max, input_est, tools_est, remaining,
                    )
                ctx.litellm_request["max_completion_tokens"] = new_max
