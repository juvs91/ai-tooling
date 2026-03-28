"""Tool Call Validator Transformer (RC-5)

Intercepts outgoing tool_use blocks BEFORE they reach Claude Code and validates
their parameters against the real CC schema. For AskUserQuestion — the tool most
prone to parameter misuse by non-Claude models — it auto-corrects malformed calls
so the user never sees an InputValidationError.

Auto-correction cases handled (all observed in production):
  • {"question": "…"}           → wraps inner-field into questions array
  • {"questions": "…"}          → converts flat string to array of one question
  • {"questions": [{…}]}        → patches each item for missing required fields
  • options with only one item  → duplicates it to satisfy minItems: 2

Runs AFTER UniversalToolExtractionTransformer (tools already normalised to
tool_use blocks) and BEFORE the response reaches Claude Code.
"""
from __future__ import annotations

import logging
from typing import Any

from llm.pipeline import Transformer, TransformContext
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

class ToolCallValidatorTransformer(Transformer):
    """Validate and auto-correct CC workflow tool_use parameters before forwarding.

    Runs as a RESPONSE transformer after UniversalToolExtractionTransformer.
    Currently handles AskUserQuestion; the pattern is extensible to other tools.
    """

    @property
    def name(self) -> str:
        return "tool_call_validator"

    async def transform(self, request: object, ctx: TransformContext) -> None:
        content = getattr(request, "content", None)
        if not isinstance(content, list):
            return

        for block in content:
            if not isinstance(block, dict):
                continue
            if bget(block, "type") != "tool_use":
                continue
            tool_name = bget(block, "name")
            if tool_name == "AskUserQuestion":
                self._validate_ask_user_question(block)

    def _validate_ask_user_question(self, block: dict) -> None:
        raw = block.get("input")
        corrected, corrections = _correct_ask_user_question(raw)

        if not corrections:
            logger.debug("[tool-call-validator] AskUserQuestion: params valid, no corrections needed")
            return

        logger.warning(
            "[tool-call-validator] AskUserQuestion: auto-corrected %d issue(s): %s",
            len(corrections), "; ".join(corrections),
        )
        block["input"] = corrected
