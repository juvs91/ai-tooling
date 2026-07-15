"""Structural validation for native Anthropic tool_use blocks.

STRUCTURAL_VALIDATORS: dict keyed by field name.
Value: (predicate_fn, fixer_fn).
  predicate_fn(block) -> bool : True if a violation is found
  fixer_fn(block)     -> str  : mutates block in-place, returns a message string

To add a new rule: define a predicate + fixer pair, then add an entry to the dict.

MALFORMED_STORE: in-memory ring buffer of malformed blocks detected this session.
Used for the retry-with-few-shot path in proxy.py and for telemetry analysis.
Capped at _MAX_STORE_SIZE; oldest entries drop when the cap is reached.

Ref: ADR-0016-native-tool-use-structural-validation.md
"""
from __future__ import annotations

import json
import uuid
from typing import Callable

_FEW_SHOT_TOOL_EXAMPLES = [
    {"type": "tool_use", "id": "toolu_01abc", "name": "Read",  "input": {"file_path": "/path/to/file.py"}},
    {"type": "tool_use", "id": "toolu_02def", "name": "Bash",  "input": {"command": "ls -la /project"}},
    {"type": "tool_use", "id": "toolu_03ghi", "name": "Write", "input": {"file_path": "/path/to/file.py", "content": "print('hello')"}},
]

MALFORMED_STORE: list[dict] = []
_MAX_STORE_SIZE = 500


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _gen_tool_id() -> str:
    return "toolu_" + uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Predicates — return True when the block violates the structural rule
# ---------------------------------------------------------------------------

def _id_missing(block: dict) -> bool:
    return not block.get("id")


def _input_not_dict(block: dict) -> bool:
    return not isinstance(block.get("input"), dict)


def _name_invalid(block: dict) -> bool:
    return not isinstance(block.get("name"), str) or not (block.get("name") or "").strip()


# ---------------------------------------------------------------------------
# Fixers — mutate block in-place, return a human-readable message
# ---------------------------------------------------------------------------

def _fix_id(block: dict) -> str:
    block["id"] = _gen_tool_id()
    return "id missing — generated"


def _fix_input(block: dict) -> str:
    original_type = type(block.get("input")).__name__
    block["input"] = {"raw": block["input"]} if block.get("input") is not None else {}
    return f"input was {original_type} — coerced to dict"


def _warn_name(block: dict) -> str:
    return "name missing/invalid — cannot auto-fix"


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

STRUCTURAL_VALIDATORS: dict[str, tuple[Callable[[dict], bool], Callable[[dict], str]]] = {
    "id":    (_id_missing,     _fix_id),
    "input": (_input_not_dict, _fix_input),
    "name":  (_name_invalid,   _warn_name),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_structural_validation(block: dict) -> list[str]:
    """Apply all structural rules to a tool_use block.

    Each fixer mutates the block in-place. Returns a list of messages for every
    violation found — empty list means the block was structurally valid.
    """
    return [
        fix(block)
        for _check, fix in STRUCTURAL_VALIDATORS.values()
        if _check(block)
    ]


def record_malformed_block(block: dict, corrections: list[str]) -> None:
    """Record metadata about a malformed block for retry/analysis.

    Stores a lightweight summary (not the full block) to avoid memory bloat.
    Drops the oldest entry when _MAX_STORE_SIZE is reached.
    """
    if len(MALFORMED_STORE) >= _MAX_STORE_SIZE:
        MALFORMED_STORE.pop(0)
    MALFORMED_STORE.append({
        "name":         block.get("name"),
        "id":           block.get("id"),
        "input_type":   type(block.get("input")).__name__,
        "corrections":  list(corrections),
    })


def pop_malformed_blocks() -> list[dict]:
    """Return and clear the malformed block store.

    Called by _try_structural_correction() in proxy.py before the retry call.
    """
    blocks = list(MALFORMED_STORE)
    MALFORMED_STORE.clear()
    return blocks


def build_correction_prompt(malformed_blocks: list[dict]) -> str:
    """Generate a few-shot correction prompt for malformed tool_use blocks.

    The prompt includes native Anthropic format examples + a summary of what
    was wrong, so the model has enough context to produce valid output.
    """
    examples = "\n".join(json.dumps(ex) for ex in _FEW_SHOT_TOOL_EXAMPLES)
    malformed_summary = "\n".join(
        f"- {b['name']}: {', '.join(b['corrections'])}"
        for b in malformed_blocks
    )
    return (
        "Your previous response contained tool_use blocks with formatting errors.\n"
        "Please regenerate ONLY the corrected tool calls using the exact Anthropic native format:\n\n"
        "CORRECT FORMAT (one per line, all fields required):\n"
        f"{examples}\n\n"
        "RULES:\n"
        "- 'id' must be a non-empty string (e.g. 'toolu_01abc...')\n"
        "- 'input' must always be a JSON object {}\n"
        "- 'type' must always be 'tool_use'\n"
        "- 'name' must be the exact tool name\n\n"
        "Errors found in your previous output:\n"
        f"{malformed_summary}\n\n"
        "Regenerate the corrected tool_use blocks now:"
    )
