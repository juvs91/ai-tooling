# llm/transformers/guardrail.py
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

from llm.pipeline import Transformer, TransformContext
from utils.utils import ensure_system_note, get_tool_name


def _build_tool_enforcement_prompt(tools: list | None) -> str:
    """Build a dynamic prompt from the request's actual tools."""
    if not tools:
        return ""
    tool_names = [get_tool_name(t) for t in tools if get_tool_name(t)]
    if not tool_names:
        return ""
    return (
        f"[tool-guard] You have {len(tool_names)} tools available: {', '.join(tool_names)}\n"
        "You MUST use these tools to gather real data before answering.\n"
        "Do NOT answer from memory when a tool can provide actual information.\n"
        "If a tool fails or is unavailable, say so explicitly \u2014 do NOT fabricate.\n"
        "Cite sources (file:line) for factual claims."
    )


_ANALYSIS_REASONING_PROMPT = (
    "[analysis-guard] This is an exhaustive analysis request. You MUST:\n"
    "1. Read EVERY file before describing it — never infer from filename alone.\n"
    "2. List ALL functions/classes with their signatures (def/class name + args).\n"
    "3. Describe internal logic, not just purpose — show HOW it works.\n"
    "4. Verify claims against actual code — never fabricate modules, files, or functions.\n"
    "5. If a file doesn't exist, say so explicitly — check the filesystem first.\n"
    "6. Provide concrete numbers: line counts, function counts, parameter counts.\n"
    "7. Show your verification process — explain what you checked and how.\n"
    "8. For each module, document: imports, constants, classes, functions, interactions.\n"
    "Quality threshold: every claim must be traceable to a specific line of code."
)

_SYNTHESIS_PROMPT = (
    "[synthesis-guard] You have read all necessary files. Now produce your "
    "comprehensive analysis report.\n"
    "1. Synthesize findings from everything you've read — do NOT make tool calls.\n"
    "2. Structure your report clearly: overview, component analysis, interactions, conclusions.\n"
    "3. Cite specific files and line numbers from your earlier reads.\n"
    "4. Be exhaustive — this is the final deliverable, not an intermediate step."
)


_READ_PATH_RE = re.compile(r'"file_path"\s*:\s*"([^"]+)"')


def _detect_duplicate_reads(messages: list, threshold: int = 3) -> str | None:
    """Scan recent tool_result messages for duplicate file reads.

    Returns a warning string if any file has been read >= threshold times,
    or None if no duplicates detected. Scans last 30 messages.
    """
    path_counter: Counter = Counter()
    checked = 0
    for msg in reversed(messages or []):
        if checked >= 30:
            break
        role = (
            getattr(msg, "role", None)
            if not isinstance(msg, dict)
            else msg.get("role")
        )
        if role != "assistant":
            continue
        content = (
            getattr(msg, "content", None)
            if not isinstance(msg, dict)
            else msg.get("content")
        )
        if not isinstance(content, list):
            continue
        for block in content:
            block_type = (
                getattr(block, "type", None)
                if not isinstance(block, dict)
                else block.get("type")
            )
            if block_type != "tool_use":
                continue
            name = (
                getattr(block, "name", None)
                if not isinstance(block, dict)
                else block.get("name")
            )
            if name != "Read":
                continue
            inp = (
                getattr(block, "input", None)
                if not isinstance(block, dict)
                else block.get("input")
            )
            if isinstance(inp, dict):
                path = inp.get("file_path", "")
            elif isinstance(inp, str):
                m = _READ_PATH_RE.search(inp)
                path = m.group(1) if m else ""
            else:
                path = ""
            if path:
                path_counter[path] += 1
        checked += 1

    duplicates = {p: c for p, c in path_counter.items() if c >= threshold}
    if not duplicates:
        return None

    lines = ["[loop-guard] Duplicate file reads detected:"]
    for path, count in sorted(duplicates.items(), key=lambda x: -x[1]):
        lines.append(f"  - {path} read {count} times")
    lines.append("You already have these files' content. Focus on using what you've read to complete the task.")
    return "\n".join(lines)


class GuardrailTransformer(Transformer):
    """Inject guardrail system note + analysis tool/reasoning enforcement."""

    @property
    def name(self) -> str:
        return "guardrail"

    def __init__(self, guard_system: str) -> None:
        self._guard_system = guard_system

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        ensure_system_note(request, self._guard_system)

        if ctx.analysis_phase in ("ANALYZING", "READ"):
            # Tool enforcement: force model to use available tools (appropriate for reading phase)
            prompt = _build_tool_enforcement_prompt(getattr(request, "tools", None))
            if prompt:
                ensure_system_note(request, prompt)
                logger.info("[analysis-guard] Injected tool enforcement prompt")
            # Reasoning enforcement: force exhaustive file reading
            ensure_system_note(request, _ANALYSIS_REASONING_PROMPT)
            logger.info("[analysis-guard] Injected reasoning enforcement prompt")

        elif ctx.analysis_phase == "SYNTHESIZING":
            # Strip tools: synthesis should produce text, not tool calls.
            # Tool definitions consume ~30K tokens (47% of DeepSeek-R1's 64K window).
            if getattr(request, "tools", None):
                tool_count = len(request.tools)
                request.tools = None
                request.tool_choice = None
                logger.info("[analysis-guard] SYNTHESIZING: stripped %d tools", tool_count)
            # Synthesis prompt: produce report from context
            ensure_system_note(request, _SYNTHESIS_PROMPT)
            logger.info("[analysis-guard] Injected synthesis prompt")

        # Loop guard: detect and warn about duplicate file reads
        loop_warning = _detect_duplicate_reads(getattr(request, "messages", []))
        if loop_warning:
            ensure_system_note(request, loop_warning)
            # Count duplicate files (each line after header = one dup file)
            dup_file_count = loop_warning.count("\n  - ")
            if dup_file_count >= 3:
                # Hard enforcement: drop Read tool to force synthesis
                tools = getattr(request, "tools", None)
                if tools:
                    before = len(tools)
                    request.tools = [t for t in tools if get_tool_name(t) != "Read"]
                    after = len(request.tools)
                    if after < before:
                        logger.warning(
                            "[loop-guard] HARD: Dropped Read tool (%d dup files, tools %d→%d)",
                            dup_file_count, before, after,
                        )
                    else:
                        logger.warning("[loop-guard] SOFT: %d dup files (Read tool not in toolset)", dup_file_count)
                else:
                    logger.warning("[loop-guard] SOFT: %d dup files (no tools)", dup_file_count)
            else:
                logger.warning("[loop-guard] SOFT: Injected warning (%d dup files)", dup_file_count)
