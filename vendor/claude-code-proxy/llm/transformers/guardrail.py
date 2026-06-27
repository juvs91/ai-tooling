# llm/transformers/guardrail.py
from __future__ import annotations

import json as _json
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

_SYNTHESIS_PROMPT_WITH_TOOLS = (
    "[synthesis-guide] You have read significant data. Begin synthesizing your findings into a "
    "comprehensive written analysis.\n\n"
    "PRIORITY: Write your synthesis now. Reference (file:line) for every factual claim.\n"
    "If you need to verify a specific fact before citing it, you may use tools — but minimize tool calls.\n"
    "Structure: OVERVIEW → COMPONENTS → FINDINGS → RECOMMENDATIONS\n"
    "Do not speculate about code you have not read."
)


_READ_PATH_RE = re.compile(r'"file_path"\s*:\s*"([^"]+)"')
_BASH_CMD_RE = re.compile(r'"command"\s*:\s*"([^"]+)"')

# CC tools that read remote/file resources — all treated as equivalent for loop detection
_READ_LIKE_TOOLS: frozenset[str] = frozenset({
    "Read", "ReadMcpResourceTool", "ReadMcpResourceDirTool", "ListMcpResourcesTool",
})


def _detect_dropped_tool_calls(messages: list) -> str | None:
    """Detect tool_use calls in the last assistant turn that got no tool_result.

    When the proxy silently drops a hallucinated tool call, CC never receives
    the tool_use block and never sends a tool_result. The model keeps retrying
    the same (unavailable) tool because it received no feedback.

    Returns a warning note if unmatched tool_use blocks are detected, else None.
    """
    if not messages:
        return None

    # Collect tool_result IDs from all user messages (last 10 turns)
    result_ids: set[str] = set()
    checked = 0
    for msg in reversed(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if role == "user" and isinstance(content, list):
            for block in content:
                btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                if btype == "tool_result":
                    tid = block.get("tool_use_id") if isinstance(block, dict) else getattr(block, "tool_use_id", None)
                    if tid:
                        result_ids.add(tid)
        checked += 1
        if checked >= 10:
            break

    # Find the last assistant message with tool_use blocks
    for msg in reversed(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role != "assistant":
            continue
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        dropped = []
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype != "tool_use":
                continue
            tid = block.get("id") if isinstance(block, dict) else getattr(block, "id", None)
            name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            if tid and tid not in result_ids:
                dropped.append(name or "unknown")
        if dropped:
            names = ", ".join(dict.fromkeys(dropped))  # dedupe preserving order
            return (
                f"[proxy-guard] Your previous tool call(s) were not executed: {names}. "
                "These tools are not available in this session. "
                "Do NOT retry them — choose a different approach to complete the task."
            )
        break  # only check the last assistant turn

    return None



def _detect_consistently_failing_tools(
    messages: list, threshold: int = 2, window: int = 20
) -> dict[str, int]:
    """Detect tools that have returned errors >= threshold times in recent history.

    Checks both is_error=True (Anthropic passthrough format) and content starting
    with '[ERROR]' (LiteLLM path, added by converters.py).

    Returns {tool_name: error_count} for tools exceeding the threshold.
    """
    recent = (messages or [])[-window:]

    # Pass 1: build complete {tool_use_id → tool_name} from all assistant messages
    id_to_name: dict[str, str] = {}
    for msg in recent:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if role != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype != "tool_use":
                continue
            bid = block.get("id") if isinstance(block, dict) else getattr(block, "id", None)
            bname = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            if bid and bname:
                id_to_name[bid] = bname

    if not id_to_name:
        return {}

    # Pass 2: count errors from tool_result blocks in user messages
    error_counts: Counter = Counter()
    for msg in recent:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if role != "user" or not isinstance(content, list):
            continue
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype != "tool_result":
                continue
            tid = block.get("tool_use_id") if isinstance(block, dict) else getattr(block, "tool_use_id", None)
            tool_name = id_to_name.get(tid)
            if not tool_name:
                continue
            # Passthrough path: is_error flag
            is_err = block.get("is_error") if isinstance(block, dict) else getattr(block, "is_error", False)
            if is_err:
                error_counts[tool_name] += 1
                continue
            # LiteLLM path: [ERROR] prefix added by converters.py
            result_content = block.get("content") if isinstance(block, dict) else getattr(block, "content", None)
            text = result_content if isinstance(result_content, str) else ""
            if text.startswith("[ERROR]"):
                error_counts[tool_name] += 1
                continue
            # Silent/null path: content is absent (None) — indicates communication failure,
            # not a tool that legitimately returns empty output (those return "" or a message).
            if result_content is None:
                error_counts[tool_name] += 1

    return {name: count for name, count in error_counts.items() if count >= threshold}


def _detect_duplicate_reads(messages: list, threshold: int = 3) -> str | None:
    """Scan recent assistant turns for duplicate calls to any read-like tool.

    Covers Read (file), ReadMcpResourceTool, ReadMcpResourceDirTool, and
    ListMcpResourcesTool — all tools that fetch remote/file content idempotently.
    Returns a warning string if any resource path has been read >= threshold times,
    or None if no duplicates detected. Scans last 50 messages.
    """
    path_counter: Counter = Counter()
    checked = 0
    for msg in reversed(messages or []):
        if checked >= 50:
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
            if name not in _READ_LIKE_TOOLS:
                continue
            inp = (
                getattr(block, "input", None)
                if not isinstance(block, dict)
                else block.get("input")
            )
            if isinstance(inp, dict):
                # Read → file_path; MCP resource tools → uri or server+uri
                path = inp.get("file_path") or inp.get("uri") or str(inp)
            elif isinstance(inp, str):
                m = _READ_PATH_RE.search(inp)
                path = m.group(1) if m else inp[:120]
            else:
                path = str(inp)
            if path:
                path_counter[path] += 1
        checked += 1

    duplicates = {p: c for p, c in path_counter.items() if c >= threshold}
    if not duplicates:
        return None

    lines = ["[loop-guard] Duplicate read-like tool calls detected:"]
    for path, count in sorted(duplicates.items(), key=lambda x: -x[1]):
        lines.append(f"  - {path!r} called {count} times")
    lines.append("You already have this content. Focus on using what you've read to complete the task.")
    return "\n".join(lines)


def _detect_stuck_tool_calls(messages: list, threshold: int = 2, window: int = 20) -> dict[str, int]:
    """Detect tools called with IDENTICAL inputs >= threshold times in recent history.

    Catches idempotent loops where the response is technically successful but
    semantically useless (e.g. "Directory listing is not enabled in this build.").
    These slip past _detect_consistently_failing_tools (is_error=False, no [ERROR]
    prefix, content not None).

    Returns {tool_name: max_repeat_count} for tools with repeated identical calls.
    """
    recent = (messages or [])[-window:]
    call_counter: Counter = Counter()

    for msg in recent:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if role != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype != "tool_use":
                continue
            name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            inp = block.get("input") if isinstance(block, dict) else getattr(block, "input", None)
            if not name:
                continue
            try:
                input_key = _json.dumps(inp, sort_keys=True) if isinstance(inp, dict) else str(inp or "")
            except Exception:
                input_key = str(inp)
            call_counter[(name, input_key)] += 1

    stuck: dict[str, int] = {}
    for (name, _), count in call_counter.items():
        if count >= threshold:
            if name not in stuck or stuck[name] < count:
                stuck[name] = count
    return stuck


def _detect_duplicate_bash_commands(messages: list, threshold: int = 3) -> str | None:
    """Detect the same Bash command run >= threshold times in recent history.

    Returns a warning string if duplicates found, else None. Scans last 40 messages.
    Does NOT hard-enforce by default — Bash is used for many unrelated operations,
    so dropping it entirely is too aggressive. Hard enforcement only fires when
    >= 2 distinct commands are each looping (model is broadly stuck, not just retrying once).
    """
    cmd_counter: Counter = Counter()
    checked = 0
    for msg in reversed(messages or []):
        if checked >= 40:
            break
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role != "assistant":
            checked += 1
            continue
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if not isinstance(content, list):
            checked += 1
            continue
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype != "tool_use":
                continue
            name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            if name != "Bash":
                continue
            inp = block.get("input") if isinstance(block, dict) else getattr(block, "input", None)
            if isinstance(inp, dict):
                cmd = inp.get("command", "").strip()
            elif isinstance(inp, str):
                m = _BASH_CMD_RE.search(inp)
                cmd = m.group(1).strip() if m else ""
            else:
                cmd = ""
            if cmd:
                cmd_counter[cmd] += 1
        checked += 1

    duplicates = {c: n for c, n in cmd_counter.items() if n >= threshold}
    if not duplicates:
        return None

    lines = ["[bash-loop-guard] Repeated Bash commands detected:"]
    for cmd, count in sorted(duplicates.items(), key=lambda x: -x[1]):
        display = cmd[:80] + "..." if len(cmd) > 80 else cmd
        lines.append(f"  - ({count}x) {display}")
    lines.append(
        "Running the same command repeatedly does not produce new information. "
        "Change your approach: try a different command, read the output you already have, "
        "or proceed with what you know."
    )
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
            # Do NOT strip tools — model may still need to verify specific claims.
            # Tool stripping creates a permanent halt with no recovery path.
            # Override F in intent_classifier.py handles phase reset if agent calls a tool.
            ensure_system_note(request, _SYNTHESIS_PROMPT_WITH_TOOLS)
            logger.info("[analysis-guard] Injected synthesis prompt (tools kept)")

        # Generic error-loop guard: block any tool that has consistently returned errors
        failing_tools = _detect_consistently_failing_tools(getattr(request, "messages", []))
        if failing_tools:
            tools_now = getattr(request, "tools", None) or []
            available = [get_tool_name(t) for t in tools_now if get_tool_name(t)]
            for tool_name, count in failing_tools.items():
                note = (
                    f"[proxy-guard] '{tool_name}' has returned errors {count} time(s). "
                    f"This tool is blocked for this turn. "
                    f"Review your available tools and select the most appropriate one: "
                    f"{', '.join(available)}."
                )
                ensure_system_note(request, note)
                if tools_now:
                    request.tools = [t for t in request.tools if get_tool_name(t) != tool_name]
                logger.warning(
                    "[error-loop-guard] Blocked '%s' after %d error(s)", tool_name, count
                )

        # Retroactive feedback: warn if last assistant turn had unmatched tool_use blocks
        drop_warning = _detect_dropped_tool_calls(getattr(request, "messages", []))
        if drop_warning:
            ensure_system_note(request, drop_warning)
            logger.warning("[drop-guard] %s", drop_warning)

        # Stuck-loop guard: detect identical tool calls (same input, no progress)
        stuck_tools = _detect_stuck_tool_calls(getattr(request, "messages", []))
        if stuck_tools:
            tools_now = getattr(request, "tools", None) or []
            available = [get_tool_name(t) for t in tools_now if get_tool_name(t)]
            for tool_name, count in stuck_tools.items():
                note = (
                    f"[proxy-guard] '{tool_name}' was called {count} time(s) with identical "
                    f"input and produced no new information. This tool is blocked for this turn. "
                    f"The operation is unavailable or the input is incorrect — do NOT retry it. "
                    f"Use an alternative from: {', '.join(available)}."
                )
                ensure_system_note(request, note)
                if tools_now:
                    request.tools = [t for t in request.tools if get_tool_name(t) != tool_name]
                logger.warning(
                    "[stuck-loop-guard] Blocked '%s' after %d identical call(s)", tool_name, count
                )

        # Loop guard: detect and warn about duplicate calls to read-like tools
        loop_warning = _detect_duplicate_reads(getattr(request, "messages", []))
        if loop_warning:
            ensure_system_note(request, loop_warning)
            dup_file_count = loop_warning.count("\n  - ")
            if dup_file_count >= 1:
                # Hard enforcement: drop ALL read-like tools (Read + MCP resource tools)
                tools = getattr(request, "tools", None)
                if tools:
                    before = len(tools)
                    request.tools = [t for t in tools if get_tool_name(t) not in _READ_LIKE_TOOLS]
                    after = len(request.tools)
                    if after < before:
                        logger.warning(
                            "[loop-guard] HARD: Dropped read-like tools (%d dup paths, tools %d→%d)",
                            dup_file_count, before, after,
                        )
                    else:
                        logger.warning("[loop-guard] SOFT: %d dup paths (no read-like tools in toolset)", dup_file_count)
                else:
                    logger.warning("[loop-guard] SOFT: %d dup paths (no tools)", dup_file_count)
            else:
                logger.warning("[loop-guard] SOFT: Injected warning (%d dup paths)", dup_file_count)

        # Bash loop guard: detect repeated commands
        bash_loop = _detect_duplicate_bash_commands(getattr(request, "messages", []))
        if bash_loop:
            ensure_system_note(request, bash_loop)
            dup_cmd_count = bash_loop.count("\n  - ")
            if dup_cmd_count >= 2:
                # Hard enforcement: >= 2 distinct commands looping → model is broadly stuck
                tools = getattr(request, "tools", None)
                if tools:
                    before = len(tools)
                    request.tools = [t for t in tools if get_tool_name(t) != "Bash"]
                    after = len(request.tools)
                    if after < before:
                        logger.warning(
                            "[bash-loop-guard] HARD: Dropped Bash tool (%d dup commands, tools %d→%d)",
                            dup_cmd_count, before, after,
                        )
                    else:
                        logger.warning("[bash-loop-guard] SOFT: %d dup commands (Bash not in toolset)", dup_cmd_count)
                else:
                    logger.warning("[bash-loop-guard] SOFT: %d dup commands (no tools)", dup_cmd_count)
            else:
                logger.warning("[bash-loop-guard] SOFT: Injected warning (%d dup commands)", dup_cmd_count)

        # Inject grounding context from previous turns (Phase 3 enhancement)
        # Only inject for analysis phases where previous verifications are relevant
        if ctx.analysis_phase in ("ANALYZING", "READ") and ctx.session_id:
            await inject_grounding_context(request, ctx.session_id)


async def inject_grounding_context(request: Any, session_id: str) -> None:
    """Inject verified claims from previous turns into system prompt.

    This adds grounding state from previous turns to help the model avoid
    redundant verifications and leverage previously validated evidence.

    Args:
        request: The request object to inject into
        session_id: Session ID for retrieving grounding state
    """
    from llm.compressor import get_grounding_state

    if not session_id:
        return

    state = await get_grounding_state(session_id)
    if not state["verified_claims"]:
        return

    # Build context from grounding graph
    verified_entities = []
    for entity, data in state["grounding_graph"].items():
        if data.get("citations"):
            verified_entities.append(
                f"- {entity}: verified in {data.get('file', 'unknown')} "
                f"({len(data.get('citations', []))} citations)"
            )

    if not verified_entities:
        return

    grounding_note = (
        "\n\nPREVIOUSLY VERIFIED CLAIMS:\n"
        "The following entities and claims were verified in previous turns:\n"
        + "\n".join(verified_entities[:10]) +  # Limit to 10 most recent
        "\n\nUse this verified context to avoid redundant verifications."
    )

    ensure_system_note(request, grounding_note)
