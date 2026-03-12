"""Deferred Tools Transformer

Claude Code injects certain special tools (EnterPlanMode, ExitPlanMode,
TodoWrite, AskUserQuestion, etc.) only in the system prompt as an
<available-deferred-tools> XML block — NOT in the request.tools array.

Non-Claude models have no way to know about these tools because:
1. build_tool_prompt() only reads request.tools → deferred tools never appear
   in the XML tool definitions injected for no-tools models.
2. valid_names is built from request.tools → if a model somehow emits one of
   these tool calls, it gets silently dropped as "hallucinated".

This transformer runs early in the request pipeline (before ToolAllowlist and
converters) and injects minimal tool definitions for all deferred tools into
request.tools, so all downstream handling works automatically for every model
and every pipeline (passthrough, LiteLLM streaming, LiteLLM non-streaming):

- LiteLLM native-tools models (MiniMax, Deepseek, Gemini): tools appear in the
  formal tools array sent to the model, so it can call them natively.
- LiteLLM no-tools models (GLM-4.7 via XML): tools appear in the injected XML
  tool prompt via build_tool_prompt().
- Passthrough models: tools appear in valid_names used by
  extract_xml_tools_from_passthrough_response() and passthrough_xml_tool_extraction(),
  preventing tool calls from being dropped as "hallucinated".

Routing is intentionally preserved: this transformer does NOT override intent or
phase. Whatever routing is correct for the request (BUILD → MiniMax, PLAN →
GLM-4.7, etc.) is allowed to proceed normally. The only guarantee is that when a
model decides to call a deferred tool it has seen in the system prompt, the proxy
will not discard the call.
"""
from __future__ import annotations

import logging

from llm.pipeline import Transformer, TransformContext
from utils.tool_utils import extract_deferred_tool_names

logger = logging.getLogger(__name__)


class DeferredToolsTransformer(Transformer):
    """Inject <available-deferred-tools> from system prompt into request.tools.

    Runs for ALL models and ALL pipelines.
    Idempotent: skips tools already present in request.tools.
    Does NOT modify routing (intent/phase remain unchanged).
    """

    @property
    def name(self) -> str:
        return "deferred_tools"

    async def transform(self, request: object, ctx: TransformContext) -> None:
        system = getattr(request, "system", None)
        deferred = extract_deferred_tool_names(system)
        if not deferred:
            return

        existing_names: set[str] = {
            t.get("name")
            for t in (request.tools or [])
            if isinstance(t, dict) and t.get("name")
        }

        new_defs = [
            {
                "name": name,
                "description": f"Claude Code built-in workflow tool: {name}. "
                               f"Call with empty input {{}} when appropriate.",
                "input_schema": {"type": "object", "properties": {}},
            }
            for name in deferred
            if name not in existing_names
        ]

        if not new_defs:
            logger.debug(
                "[deferred-tools] %d deferred tool(s) already in request.tools: %s",
                len(deferred), deferred,
            )
            return

        request.tools = list(request.tools or []) + new_defs
        names = [d["name"] for d in new_defs]
        print(f"[deferred-tools] injected {len(names)} deferred tool(s): {', '.join(names)}", flush=True)
