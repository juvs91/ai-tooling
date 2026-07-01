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

import hashlib
import json
import logging
import uuid

from llm.compressor import get_session_deferred_tools, save_session_deferred_tools
from llm.pipeline import Transformer, TransformContext
from utils.tool_utils import extract_deferred_tool_names
from utils.utils import bget

logger = logging.getLogger(__name__)

_DEFERRED_PREFIX_SIZE = 20  # Messages to hash for deterministic session identity


def _compute_deferred_session_id(messages: list) -> str | None:
    """Compute a deterministic session ID from the conversation prefix.

    Used as a fallback when ctx.session_id is None (Claude Code CLI does not
    send X-Session-ID by default). Mirrors the compressor's approach:
    uuid5(NAMESPACE_OID, sha256(first N messages)).

    Returns None if messages is empty (nothing to hash).
    """
    if not messages:
        return None
    prefix = messages[:_DEFERRED_PREFIX_SIZE]
    raw = json.dumps(prefix, sort_keys=True, default=str)
    prefix_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return str(uuid.uuid5(uuid.NAMESPACE_OID, prefix_hash))




def _exit_plan_already_called(messages: list, window: int = 120) -> bool:
    """Return True if ExitPlanMode was already called in recent assistant history.

    Used to decide whether to strip plan-mode tools from the session cache.
    Once ExitPlanMode is called the plan session is over; until then it must
    stay available even during READ/ANALYZING intermediate turns of a multi-turn
    plan session (where the model explores files before writing the final plan).

    Window=120 matches _plan_mode_active() in intent_classifier.py.
    Plan sessions with many file reads easily exceed 20 messages; 120 gives
    sufficient coverage for typical planning sessions.
    """
    count = 0
    for msg in reversed(messages or []):
        if bget(msg, "role") != "assistant":
            continue
        content = bget(msg, "content")
        if isinstance(content, list):
            for block in content:
                if (bget(block, "type") == "tool_use"
                        and bget(block, "name") == "ExitPlanMode"):
                    return True
        count += 1
        if count >= window:
            break
    return False

# Verified input schemas for CC workflow tools that require non-empty input.
# Source of truth: ToolSearch → AskUserQuestion real schema + universal_tool_extraction.py
# _FEW_SHOT_EXAMPLES + test fixtures.
# Tools not listed here (EnterPlanMode, ExitPlanMode, Cron*, Worktree*, Task*)
# use the empty stub — they either take no input or have no verified schema in proxy.
_CC_TOOL_SCHEMAS: dict[str, dict] = {
    "AskUserQuestion": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "Questions to present to the user (1–4 items)",
                "maxItems": 4,
                "items": {
                    "type": "object",
                    # All four fields are required by Claude Code's real validator.
                    # Marking them required here guides the model to always supply them,
                    # preventing secondary validation failures after the shape is correct.
                    "required": ["question", "header", "options", "multiSelect"],
                    "additionalProperties": False,
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The full question text to display to the user",
                        },
                        "header": {
                            "type": "string",
                            "description": "Short chip label shown above the question (max 12 chars)",
                        },
                        "options": {
                            "type": "array",
                            "description": "2–4 selectable choices for this question",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "required": ["label", "description"],
                                "additionalProperties": False,
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": "Short display text for the option (1–5 words)",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "Explanation of what the option means or implies",
                                    },
                                },
                            },
                        },
                        "multiSelect": {
                            "type": "boolean",
                            "description": "True to allow selecting multiple options; False for single-select",
                        },
                    },
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

# Semantic descriptions for CC workflow tools.
# These descriptions are shown to ALL models (both no-tools XML path and native-tools path).
# For native-tools models build_tool_prompt() is never called, so the description field in
# the tool definition is the ONLY guidance the model receives — a concrete example embedded
# here is the highest-leverage fix for AskUserQuestion's question/questions naming confusion.
_CC_TOOL_DESCRIPTIONS: dict[str, str] = {
    "AskUserQuestion": (
        "Display one or more questions to the user in an interactive dialog and collect their"
        " answers. "
        "IMPORTANT — the top-level key is 'questions' (plural, an ARRAY), NOT 'question'. "
        "Each item in the array MUST have ALL FOUR fields: "
        "'question' (string — the full question text), "
        "'header' (string — short chip label, max 12 chars), "
        "'options' (array of 2–4 objects each with 'label' string and 'description' string), "
        "'multiSelect' (boolean). "
        'Example: {"questions":[{"question":"Which approach should we use?",'
        '"header":"Approach",'
        '"options":[{"label":"Simple","description":"Minimal implementation, easier to maintain"},'
        '{"label":"Robust","description":"Full implementation with error handling"}],'
        '"multiSelect":false}]}'
    ),
    "TodoWrite": (
        "Create or update the session task list. "
        "Pass 'todos' (array of objects). Each todo MUST have: "
        "'content' (string — task description), "
        "'status' (string — 'pending', 'in_progress', or 'completed'), "
        "'activeForm' (string — present-continuous form, e.g. 'Fixing bug'). "
        'Example: {"todos":[{"content":"Fix bug","status":"in_progress","activeForm":"Fixing bug"}]}'
    ),
    "WebSearch": (
        "Search the web for up-to-date information. "
        "Pass 'query' (string — the search query). "
        'Example: {"query":"python async best practices 2025"}'
    ),
    "WebFetch": (
        "Fetch and extract content from a URL. "
        "Pass 'url' (string) and 'prompt' (string — what to extract from the page). "
        'Example: {"url":"https://example.com/docs","prompt":"Extract the API reference"}'
    ),
}

# Tools that should only be injected during PLAN phase.
# Injecting these outside PLAN phase is safe (they're passive), but filtering
# them in non-PLAN turns makes it structurally impossible to accidentally
# trigger the Plans tab from a cached tool list.
_PLAN_ONLY_TOOLS: frozenset[str] = frozenset({"EnterPlanMode", "ExitPlanMode"})

# Tools always guaranteed in PLAN phase even when CC's system prompt omits them.
# Some CC project configurations (e.g. school-system) never include AskUserQuestion
# in <available-deferred-tools>, yet plan-mode models MUST be able to call it to
# surface the question dialog. TodoWrite is included for the same reason.
_PLAN_DEFAULT_TOOLS: frozenset[str] = frozenset({"AskUserQuestion", "TodoWrite"})


def _tool_name(t) -> str | None:
    return t.get("name") if isinstance(t, dict) else getattr(t, "name", None)


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

        # Resolve effective session ID: prefer explicit X-Session-ID header, fall
        # back to a deterministic UUID derived from the conversation prefix. This
        # mirrors the compressor's approach and ensures the cache works even when
        # Claude Code CLI is used without sending X-Session-ID.
        effective_sid = ctx.session_id or _compute_deferred_session_id(messages)

        # ── Step 1: Extract from CC's system prompt (primary source) ──────────
        deferred = extract_deferred_tool_names(system, messages=messages)
        _source = "system_prompt" if deferred else None

        # ── Step 2: Persist to session cache if CC sent the list ──────────────
        if deferred and effective_sid:
            await save_session_deferred_tools(effective_sid, deferred)

        # ── Step 3: Fall back to session cache if system prompt has no list ───
        if not deferred and effective_sid:
            cached = await get_session_deferred_tools(effective_sid)
            if cached:
                # Plan-mode tools (ExitPlanMode) are stripped from the cache
                # ONLY once the plan session is over, i.e. ExitPlanMode was
                # already called. Previously filtered by ctx.phase != "PLAN",
                # which broke multi-turn plan sessions: intermediate READ /
                # ANALYZING turns stripped ExitPlanMode even though the model
                # still needed it to call the tool and surface the Plan tab.
                if ctx.phase != "PLAN":
                    msgs = list(messages or [])
                    if _exit_plan_already_called(msgs):
                        cached = [n for n in cached if n not in _PLAN_ONLY_TOOLS]
                if cached:
                    deferred = cached
                    _source = "session_cache"
                    logger.debug(
                        "[deferred-tools] restored %d tool(s) from session cache (phase=%s)",
                        len(deferred), ctx.phase,
                    )

        # ── Step 4: PLAN phase guarantee ──────────────────────────────────────
        # Ensure plan-mode tools are available when plan mode is genuinely active.
        # _PLAN_DEFAULT_TOOLS (AskUserQuestion, TodoWrite) are also guaranteed
        # because some CC project configs never include them in
        # <available-deferred-tools> (RC-8).
        #
        # ctx.plan_mode_active is the single authoritative signal, set by
        # IntentClassifierTransformer (which always runs before this transformer
        # in build_request_pipeline). Signals 0-3 give unlimited coverage across
        # sessions of any length.
        #
        # NOTE: do NOT use ctx.phase == "PLAN" as a fallback here. SYNTHESIZING
        # intent maps to phase=PLAN and would inject EnterPlanMode during BUILD
        # sessions (after 6+ consecutive reads), causing plan mode re-entry when
        # plan_mode_active=False. ADR-0011.
        if ctx.plan_mode_active:
            deferred_set = set(deferred)
            extras = [
                n for n in (*_PLAN_ONLY_TOOLS, *_PLAN_DEFAULT_TOOLS)
                if n not in deferred_set
            ]
            if extras:
                deferred = list(deferred) + extras
                _source = _source or "plan_guarantee"
                logger.debug(
                    "[deferred-tools] plan_guarantee (plan_mode_active): added %s", extras
                )

        # ── Final gate: strip plan-mode controls from request.tools ──────────
        # CC sends EnterPlanMode/ExitPlanMode in request.tools for some project
        # configurations, regardless of what DeferredTools injects (Steps 1-4).
        # Strip them unconditionally when not in plan mode so Kimi can't call them.
        # Gate: keep when plan_mode_active=True (need ExitPlanMode) or intent=PLAN
        # (entering plan mode). Strip for BUILD/READ/CHAT/VERIFY/SYNTHESIZING. ADR-0012.
        _gate_active = not ctx.plan_mode_active and ctx.intent not in ("PLAN",)
        if _gate_active and request.tools:
            _before = list(request.tools)
            request.tools = [t for t in _before if _tool_name(t) not in _PLAN_ONLY_TOOLS]
            _stripped = [_tool_name(t) for t in _before if _tool_name(t) in _PLAN_ONLY_TOOLS]
            if _stripped:
                logger.info(
                    "[deferred-tools] final-gate: stripped %d plan-only tool(s) "
                    "(intent=%s): %s",
                    len(_stripped), ctx.intent, ", ".join(n for n in _stripped if n),
                )

        if not deferred:
            return

        existing_names: set[str] = {
            n for t in (request.tools or []) if (n := _tool_name(t))
        }

        new_defs = [
            {
                "name": name,
                "description": _CC_TOOL_DESCRIPTIONS.get(
                    name,
                    f"Claude Code built-in workflow tool: {name}. Use the input schema.",
                ),
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
        logger.info(
            "[deferred-tools] injected %d tool(s) via %s (phase=%s): %s",
            len(names), _source or "unknown", ctx.phase, ", ".join(names),
        )
