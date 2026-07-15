"""Tool Call Validator Transformer (RC-5)

Intercepts outgoing tool_use blocks BEFORE they reach Claude Code and validates
their parameters against the real CC schema. Auto-corrects malformed calls so the
user never sees an InputValidationError.

Tools covered (all 7 deferred CC workflow tools):
  AskUserQuestion — most prone to misuse; full schema correction
  ExitPlanMode    — strip spurious params (models pass {"reason": "..."})
  EnterPlanMode   — strip spurious params
  TodoWrite       — wrap flat content string into todos array
  WebSearch       — ensure query is a non-empty string
  WebFetch        — ensure url present; inject prompt default if missing
  NotebookEdit    — ensure required fields present

All corrections are logged at WARNING level and counted in ctx.tool_corrections
for observability via /api/stats.

Runs AFTER UniversalToolExtractionTransformer (tools already normalised to
tool_use blocks) and BEFORE the response reaches Claude Code.
"""
from __future__ import annotations

import logging
from typing import Any

from llm.pipeline import Transformer, TransformContext
from llm.transformers.structural_tool_validator import (
    apply_structural_validation,
    record_malformed_block,
)
from utils.utils import bget

logger = logging.getLogger(__name__)

# Sentinel for "caller provided nothing useful"
_MISSING = object()


# ---------------------------------------------------------------------------
# AskUserQuestion correction helpers
# ---------------------------------------------------------------------------

def _ensure_option(opt: Any) -> dict:
    """Return a valid options item, filling in missing required keys."""
    if not isinstance(opt, dict):
        text = str(opt) if opt else "Option"
        return {"label": text[:30], "description": text}
    return {
        "label":       str(opt.get("label") or "Option")[:30],
        "description": str(opt.get("description") or opt.get("label") or ""),
    }


def _ensure_question_item(item: Any, fallback_text: str = "") -> dict:
    """Return a fully-populated question object from whatever the model sent."""
    if not isinstance(item, dict):
        item = {"question": str(item) if item else fallback_text}

    question = str(item.get("question") or fallback_text or "Please clarify:")
    header   = str(item.get("header") or "Question")[:12]

    raw_opts = item.get("options")
    if isinstance(raw_opts, list) and raw_opts:
        options = [_ensure_option(o) for o in raw_opts[:4]]
    else:
        options = [
            {"label": "Yes",    "description": "Proceed with this approach"},
            {"label": "No",     "description": "Take a different approach"},
        ]

    # minItems: 2 — duplicate first option if only one was provided
    if len(options) == 1:
        options.append({"label": "Other", "description": "Different option"})

    multi = item.get("multiSelect", False)
    if not isinstance(multi, bool):
        multi = str(multi).lower() in ("true", "1", "yes")

    return {
        "question":    question,
        "header":      header,
        "options":     options,
        "multiSelect": multi,
    }


def _correct_ask_user_question(raw_input: Any) -> tuple[dict, list[str]]:
    """
    Return (corrected_input, list_of_applied_corrections).

    Handles the two primary failure modes observed in production:
      1. Model used 'question' (inner field) as top-level key.
      2. Model used 'questions' but as a string, not an array.
    Plus secondary corrections for missing item fields.
    """
    corrections: list[str] = []

    if not isinstance(raw_input, dict):
        corrections.append(f"input was {type(raw_input).__name__}, expected dict — wrapping")
        raw_input = {"question": str(raw_input)}

    questions_val = raw_input.get("questions", _MISSING)

    # RC-1: model used 'question' (singular) instead of 'questions' (plural array)
    if questions_val is _MISSING and "question" in raw_input:
        corrections.append("used 'question' (singular) as top-level key — lifting into questions array")
        questions_val = [{"question": raw_input["question"]}]
        # Carry over any other item-level keys the model may have also provided
        for k in ("header", "options", "multiSelect"):
            if k in raw_input:
                questions_val[0][k] = raw_input[k]

    # RC-2: model used 'questions' but as a flat string
    if isinstance(questions_val, str):
        corrections.append(f"'questions' was a string — converting to array of one question")
        questions_val = [{"question": questions_val}]

    # Fallback: still nothing useful
    if questions_val is _MISSING or not questions_val:
        corrections.append("no usable question data found — inserting placeholder")
        questions_val = [{"question": "Please clarify:", "header": "Question",
                          "options": [{"label": "Yes", "description": "Proceed"},
                                      {"label": "No",  "description": "Cancel"}],
                          "multiSelect": False}]

    # Ensure it's a list
    if not isinstance(questions_val, list):
        corrections.append(f"'questions' was {type(questions_val).__name__} — wrapping in list")
        questions_val = [questions_val]

    # Patch each item for missing required fields (RC-3)
    patched: list[dict] = []
    for i, item in enumerate(questions_val[:4]):  # maxItems: 4
        fixed = _ensure_question_item(item)
        if fixed != item:
            corrections.append(f"item[{i}]: patched missing fields")
        patched.append(fixed)

    return {"questions": patched}, corrections


# ---------------------------------------------------------------------------
# Transformer
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# No-param tools (ExitPlanMode, EnterPlanMode)
# ---------------------------------------------------------------------------

def _correct_no_param_tool(raw_input: Any, tool_name: str) -> tuple[dict, list[str]]:
    """Strip all parameters — these tools take no input."""
    if raw_input and raw_input != {}:
        return {}, [f"{tool_name} must have no params, got: {list(raw_input.keys()) if isinstance(raw_input, dict) else type(raw_input).__name__}"]
    return {}, []


# ---------------------------------------------------------------------------
# TodoWrite correction
# ---------------------------------------------------------------------------

def _correct_todo_write(raw_input: Any) -> tuple[dict, list[str]]:
    """Ensure todos is an array of {content, status, priority} objects."""
    corrections: list[str] = []
    if not isinstance(raw_input, dict):
        corrections.append(f"input was {type(raw_input).__name__}, expected dict — wrapping")
        raw_input = {}

    todos = raw_input.get("todos")

    # Model passed a flat string instead of array
    if isinstance(todos, str):
        corrections.append("'todos' was a string — wrapping into single-item array")
        todos = [{"content": todos, "status": "pending", "priority": "medium"}]

    # Model passed a single dict instead of array
    if isinstance(todos, dict):
        corrections.append("'todos' was a dict — wrapping into array")
        todos = [todos]

    # No todos at all
    if not todos:
        corrections.append("'todos' missing — inserting placeholder")
        todos = [{"content": "Complete the task", "status": "pending", "priority": "medium"}]

    # Patch each item for missing required fields
    patched: list[dict] = []
    for i, item in enumerate(todos):
        if not isinstance(item, dict):
            corrections.append(f"todos[{i}] was {type(item).__name__} — converting to dict")
            item = {"content": str(item), "status": "pending", "priority": "medium"}
        fixed = {
            "content":  str(item.get("content") or "Task"),
            "status":   item.get("status") or "pending",
            "priority": item.get("priority") or "medium",
        }
        if fixed != item:
            corrections.append(f"todos[{i}]: patched missing fields")
        patched.append(fixed)

    return {"todos": patched}, corrections


# ---------------------------------------------------------------------------
# WebSearch correction
# ---------------------------------------------------------------------------

def _correct_web_search(raw_input: Any) -> tuple[dict, list[str]]:
    """Ensure query is a non-empty string."""
    corrections: list[str] = []
    if not isinstance(raw_input, dict):
        corrections.append(f"input was {type(raw_input).__name__} — wrapping as query")
        return {"query": str(raw_input) if raw_input else "search"}, corrections

    query = raw_input.get("query")
    if not query or not isinstance(query, str):
        corrections.append(f"'query' missing or invalid ({type(query).__name__}) — inserting placeholder")
        raw_input = dict(raw_input)
        raw_input["query"] = "search"
    return raw_input, corrections


# ---------------------------------------------------------------------------
# WebFetch correction
# ---------------------------------------------------------------------------

def _correct_web_fetch(raw_input: Any) -> tuple[dict, list[str]]:
    """Ensure url is present and prompt has a default."""
    corrections: list[str] = []
    if not isinstance(raw_input, dict):
        corrections.append(f"input was {type(raw_input).__name__} — treating as url")
        return {"url": str(raw_input) if raw_input else "", "prompt": "Fetch the page content"}, corrections

    out = dict(raw_input)
    if not out.get("url"):
        corrections.append("'url' missing — cannot auto-correct, inserting placeholder")
        out["url"] = "https://example.com"
    if not out.get("prompt"):
        corrections.append("'prompt' missing — injecting default")
        out["prompt"] = "Fetch and summarize the page content"
    return out, corrections


# ---------------------------------------------------------------------------
# NotebookEdit correction
# ---------------------------------------------------------------------------

def _correct_notebook_edit(raw_input: Any) -> tuple[dict, list[str]]:
    """Ensure required fields are present."""
    corrections: list[str] = []
    if not isinstance(raw_input, dict):
        corrections.append(f"input was {type(raw_input).__name__} — cannot auto-correct")
        return raw_input or {}, corrections

    out = dict(raw_input)
    for field in ("notebook_path", "cell_id", "new_source"):
        if field not in out:
            corrections.append(f"'{field}' missing — inserting empty placeholder")
            out[field] = ""
    return out, corrections


# ---------------------------------------------------------------------------
# Dispatcher map
# ---------------------------------------------------------------------------

_CORRECTORS = {
    "AskUserQuestion": lambda inp: _correct_ask_user_question(inp),
    "ExitPlanMode":    lambda inp: _correct_no_param_tool(inp, "ExitPlanMode"),
    "EnterPlanMode":   lambda inp: _correct_no_param_tool(inp, "EnterPlanMode"),
    "TodoWrite":       lambda inp: _correct_todo_write(inp),
    "WebSearch":       lambda inp: _correct_web_search(inp),
    "WebFetch":        lambda inp: _correct_web_fetch(inp),
    "NotebookEdit":    lambda inp: _correct_notebook_edit(inp),
}


class ToolCallValidatorTransformer(Transformer):
    """Validate and auto-correct all 7 CC deferred workflow tools before forwarding.

    Runs as a RESPONSE transformer after UniversalToolExtractionTransformer.
    Each correction is logged at WARNING level and counted in ctx for /api/stats.
    """

    @property
    def name(self) -> str:
        return "tool_call_validator"

    async def transform(self, request: object, ctx: TransformContext) -> None:
        content = getattr(request, "content", None)
        if not isinstance(content, list):
            return

        total_corrections = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            if bget(block, "type") != "tool_use":
                continue
            tool_name = bget(block, "name")

            # Structural: applies to ALL tool_use blocks before tool-specific correction
            struct_corrections = apply_structural_validation(block)
            if struct_corrections:
                logger.warning(
                    "[tool-call-validator] %s: structural: %s",
                    tool_name, "; ".join(struct_corrections),
                )
                record_malformed_block(block, struct_corrections)
                ctx.quality_issues.append(f"structural:{tool_name}")
                total_corrections += len(struct_corrections)

            # Tool-specific: only the 7 deferred tools
            corrector = _CORRECTORS.get(tool_name)
            if corrector:
                total_corrections += self._apply_correction(block, tool_name, corrector)

        if total_corrections:
            # Surface correction count for quality recorder / /api/stats
            ctx.quality_issues.append(f"tool_corrections:{total_corrections}")

    def _apply_correction(self, block: dict, tool_name: str, corrector) -> int:
        raw = block.get("input") or {}
        corrected, corrections = corrector(raw)

        if not corrections:
            logger.debug("[tool-call-validator] %s: params valid", tool_name)
            return 0

        logger.warning(
            "[tool-call-validator] %s: auto-corrected %d issue(s): %s",
            tool_name, len(corrections), "; ".join(corrections),
        )
        block["input"] = corrected
        return len(corrections)
