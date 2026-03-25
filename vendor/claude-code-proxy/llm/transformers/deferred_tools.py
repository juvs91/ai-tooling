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

from llm.compressor import get_session_deferred_tools, save_session_deferred_tools
from llm.pipeline import Transformer, TransformContext
from utils.tool_utils import extract_deferred_tool_names

logger = logging.getLogger(__name__)

# Verified input schemas for CC workflow tools that require non-empty input.
# Source of truth: universal_tool_extraction.py _FEW_SHOT_EXAMPLES + test fixtures.
# Tools not listed here (EnterPlanMode, ExitPlanMode, Cron*, Worktree*, Task*)
# use the empty stub — they either take no input or have no verified schema in proxy.
_CC_TOOL_SCHEMAS: dict[str, dict] = {
    "AskUserQuestion": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "Questions to present to the user",
                "items": {
                    "type": "object",
                    "properties": {
                        "question":    {"type": "string"},
                        "header":      {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label":       {"type": "string"},
                                    "description": {"type": "string"},
                                },
                            },
                        },
                        "multiSelect": {"type": "boolean"},
                    },
                    "required": ["question"],
                },
            },
        },
        "required": ["questions"],
    },
    "TodoWrite": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "Array of todo items",
                "items": {
                    "type": "object",
                    "properties": {
                        "content":    {"type": "string"},
                        "status":     {"type": "string"},
                        "activeForm": {"type": "string"},
                    },
                    "required": ["content", "status", "activeForm"],
                },
            },
        },
        "required": ["todos"],
    },
    "WebSearch": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    },
    "WebFetch": {
        "type": "object",
        "properties": {
            "url":    {"type": "string", "description": "URL to fetch"},
            "prompt": {"type": "string", "description": "What to extract from the page"},
        },
        "required": ["url", "prompt"],
    },
    "NotebookEdit": {
        "type": "object",
        "properties": {
            "notebook_path": {"type": "string"},
            "new_source":    {"type": "string"},
            "cell_type":     {"type": "string"},
            "edit_mode":     {"type": "string"},
        },
        "required": ["notebook_path", "new_source"],
    },
}

# Tools that should only be injected during PLAN phase.
# Injecting these outside PLAN phase is safe (they're passive), but filtering
# them in non-PLAN turns makes it structurally impossible to accidentally
# trigger the Plans tab from a cached tool list.
_PLAN_ONLY_TOOLS: frozenset[str] = frozenset({"EnterPlanMode", "ExitPlanMode"})


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
        messages = getattr(request, "messages", None)

        # ── Step 1: Extract from CC's system prompt (primary source) ──────────
        deferred = extract_deferred_tool_names(system, messages=messages)

        # ── Step 2: Persist to session cache if CC sent the list ──────────────
        if deferred and ctx.session_id:
            await save_session_deferred_tools(ctx.session_id, deferred)

        # ── Step 3: Fall back to session cache if system prompt has no list ───
        if not deferred and ctx.session_id:
            cached = await get_session_deferred_tools(ctx.session_id)
            if cached:
                # Plan-mode tools are only restored during PLAN phase.
                # Other phases get the full cached list minus plan-only tools.
                if ctx.phase != "PLAN":
                    cached = [n for n in cached if n not in _PLAN_ONLY_TOOLS]
                if cached:
                    deferred = cached
                    logger.debug(
                        "[deferred-tools] restored %d tool(s) from session cache",
                        len(deferred),
                    )

        # ── Step 4: PLAN phase guarantee ──────────────────────────────────────
        # Even if session cache is empty (brand-new session, first turn),
        # always ensure plan-mode tools are available during PLAN phase so
        # EnterPlanMode can never be silently dropped by stream validation.
        if ctx.phase == "PLAN":
            deferred_set = set(deferred)
            extras = [n for n in _PLAN_ONLY_TOOLS if n not in deferred_set]
            if extras:
                deferred = list(deferred) + extras
                logger.debug(
                    "[deferred-tools] PLAN phase guarantee: added %s", extras
                )

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
                               f"Use the input schema.",
                "input_schema": _CC_TOOL_SCHEMAS.get(name, {"type": "object", "properties": {}}),
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
