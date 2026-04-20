"""Plan Mode Guard Transformer

Intercepts tool_use blocks in the MODEL RESPONSE before they reach Claude Code
and blocks disallowed operations during plan mode.

Unlike intent_enforcement (which injects advisory text), this transformer
physically prevents Edit/Write/Bash-write calls by replacing the tool_use block
with a text block. Claude Code never sees the blocked call and never executes it.

The blocked text is stored in conversation history, so the model sees it on the
next turn and can adapt (finish plan, call ExitPlanMode, use read-only tools).

Allowed in plan mode:
  - Read, Glob, Grep, LS, WebSearch, WebFetch (read-only research)
  - Bash with read-only commands (git log, ls, cat, find, head, tail, wc, grep, etc.)
  - Write / Edit ONLY when file_path matches *.claude/plans/*.md
  - EnterPlanMode, ExitPlanMode, AskUserQuestion, TodoWrite
  - Agent, Task* tools (subagents operate in their own context)

Blocked in plan mode:
  - Edit, MultiEdit, Write, NotebookEdit targeting non-plan files
  - Bash with write patterns (>, >>, tee, cp, mv, rm, install, pip, npm install, etc.)
"""
from __future__ import annotations

import logging
import re
from typing import Any

from llm.pipeline import Transformer, TransformContext
from utils.utils import bget

logger = logging.getLogger(__name__)

# Matches plan files: anything ending in .claude/plans/<name>.md
# Handles both absolute paths and relative paths
_PLAN_FILE_RE = re.compile(r'(^|/)\.claude/plans/[^/]+\.md$')

# Tool names that modify files — blocked unless file_path is a plan file
_FILE_WRITE_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})

# Bash patterns that indicate writes — blocked in plan mode
# Ordered from most specific to least to avoid false positives
_BASH_WRITE_PATTERNS = (
    " >> ",   " > ",    "| tee ",
    "cp ",    "mv ",    "rm ",
    "mkdir ", "rmdir ", "touch ",
    "npm install", "pip install", "pip3 install",
    "yarn add", "brew install",
    "git commit", "git push", "git reset", "git checkout --",
)

# Tools always allowed regardless of plan mode
_ALWAYS_ALLOWED = frozenset({
    "EnterPlanMode", "ExitPlanMode",
    "AskUserQuestion", "TodoWrite",
    "Read", "Glob", "Grep", "LS",
    "WebSearch", "WebFetch",
    "Agent", "Task", "TaskOutput", "TaskStop",
})


def _is_plan_file(file_path: str) -> bool:
    return bool(_PLAN_FILE_RE.search(file_path))


def _bash_has_write(cmd: str) -> tuple[bool, str]:
    """Return (is_write, matched_pattern). Case-insensitive check."""
    cmd_lower = cmd.lower()
    for pattern in _BASH_WRITE_PATTERNS:
        if pattern.lower() in cmd_lower:
            return True, pattern.strip()
    return False, ""


def _make_text_block(block_id: str, text: str) -> dict:
    """Return a text block that replaces a blocked tool_use."""
    return {"type": "text", "text": text}


class PlanModeGuardTransformer(Transformer):
    """Block disallowed tool_use blocks during plan mode before they reach Claude Code.

    Runs in the RESPONSE pipeline after UniversalToolExtractionTransformer and
    ToolCallValidatorTransformer, before GroundingValidatorTransformer.
    """

    @property
    def name(self) -> str:
        return "plan_mode_guard"

    async def transform(self, response: Any, ctx: TransformContext) -> None:
        if not ctx.plan_mode_active:
            return

        content = getattr(response, "content", None)
        if not isinstance(content, list):
            return

        new_content: list[dict] = []
        blocked_count = 0

        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            if bget(block, "type") != "tool_use":
                new_content.append(block)
                continue

            tool_name = bget(block, "name", "")
            tool_input = block.get("input") or {}
            block_id = block.get("id", "")

            # Always-allowed tools pass through
            if tool_name in _ALWAYS_ALLOWED:
                new_content.append(block)
                continue

            # File-write tools: allow only plan files
            if tool_name in _FILE_WRITE_TOOLS:
                file_path = tool_input.get("file_path", "")
                if _is_plan_file(file_path):
                    new_content.append(block)
                    continue
                msg = (
                    f"[PLAN MODE] {tool_name}({file_path!r}) blocked — "
                    "only .claude/plans/*.md files may be modified during planning. "
                    "Finish writing your plan, then call ExitPlanMode to implement."
                )
                logger.warning("[plan-guard] Blocked %s(%r) in PLAN phase", tool_name, file_path)
                new_content.append(_make_text_block(block_id, msg))
                blocked_count += 1
                continue

            # Bash: block write-pattern commands
            if tool_name == "Bash":
                cmd = tool_input.get("command", "")
                is_write, matched = _bash_has_write(cmd)
                if is_write:
                    preview = cmd[:80].replace("\n", " ")
                    msg = (
                        f"[PLAN MODE] Bash write blocked (matched '{matched}'): `{preview}` — "
                        "only read-only commands are allowed during planning "
                        "(git log, ls, cat, grep, find, head, tail, wc). "
                        "Call ExitPlanMode first to run write commands."
                    )
                    logger.warning("[plan-guard] Blocked Bash write in PLAN phase: %r", preview)
                    new_content.append(_make_text_block(block_id, msg))
                    blocked_count += 1
                    continue
                # Read-only Bash passes through
                new_content.append(block)
                continue

            # All other tools pass through (Skill, Agent variants, etc.)
            new_content.append(block)

        if blocked_count:
            response.content = new_content
            logger.info("[plan-guard] Blocked %d tool_use call(s) in PLAN phase", blocked_count)
