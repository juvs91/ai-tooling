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
    "[code-analysis-guard] CRITICAL: Analysis must be EVIDENCE-BASED, not speculative.\n\n"
    "REQUIRED WORKFLOW:\n"
    "1. FIRST: Use Glob/Grep to discover ACTUAL file structure (extensions, paths)\n"
    "2. SECOND: Use Read on files that EXIST\n"
    "3. THIRD: Write analysis citing SPECIFIC tool outputs\n\n"
    "EVIDENCE EXAMPLES:\n"
    "- GOOD: 'Read server.py and found a race condition at line 45'\n"
    "- GOOD: 'Grep returned 3 files with this pattern'\n"
    "- BAD: 'The compressor has a bug' (WHAT file? WHAT line? WHAT did you read?)\n"
    "- BAD: 'server.ts line 50 shows X' (did you ACTUALLY read that file? prove it)\n\n"
    "If a file doesn't exist after trying to read it, SAY SO - don't invent.\n\n"
    "CRITICAL RULES:\n"
    "1. NEVER claim a file/function exists without reading it FIRST\n"
    "2. ALWAYS cite (file:line) for:\n"
    "   - Function signatures: 'def foo()' → (server.py:123)\n"
    "   - Bug locations: 'race condition' → (compressor.py:259-280)\n"
    "   - Metrics: '47% faster' → show measurement method\n"
    "3. If you mention a bug, you MUST:\n"
    "   - Read the exact code section\n"
    "   - Explain WHY it's a bug (concrete failure mode)\n"
    "   - Provide a minimal fix (diff format)\n\n"
    "4. Pattern matching:\n"
    "   - Similar code in multiple files? Read each one.\n"
    "   - Claiming 'X does Y'? Verify with actual code, not assumptions.\n\n"
    "5. Specificity beats generalities:\n"
    "   - 'handle_streaming() has 787 lines' > 'streaming is large'\n"
    "   - 'cache.get() then cache.set()' > 'uses caching'\n\n"
    "Quality threshold: Every claim MUST be verifiable by reading the cited file:line."
)

_SYNTHESIS_PROMPT = (
    "[synthesis-guard] You have gathered all data via tools. Produce final report.\n\n"
    "REQUIRED STRUCTURE:\n"
    "1. OVERVIEW: System architecture (3-5 sentences, with file references)\n"
    "2. COMPONENTS: Each module with:\n"
    "   - File path (server.py, router/llm_router.py, etc.)\n"
    "   - Purpose (1 line)\n"
    "   - Key classes/functions (signatures + line ranges)\n"
    "   - Interactions (calls X, receives from Y)\n"
    "3. FINDINGS: For each bug/pattern:\n"
    "   - Claim: What IS the issue?\n"
    "   - Evidence: Exact code snippet (file:line)\n"
    "   - Verification: How did you confirm it's real?\n"
    "4. RECOMMENDATIONS: Prioritized by impact (CRITICAL > HIGH > LOW)\n\n"
    "NO TOOL CALLS — Use only data you already read.\n"
    "CITATION FORMAT: [filename.py:line] for all factual claims."
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
