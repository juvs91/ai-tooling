# llm/transformers/intent_classifier.py
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from llm.pipeline import Transformer, TransformContext
from router.llm_router import (
    classify_intent,
    content_to_rough_text,
    get_last_user_text,
    is_analysis_request,
    _regex_fallback_intent,
    _is_pure_tool_result,
    BUILDING_RE,
)
from config import ClassifierConfig, PolicyConfig
from utils.metrics import metrics


WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})
READ_TOOLS = frozenset({"Read", "Grep", "Glob", "Bash", "WebFetch", "WebSearch"})


def _detect_phase(messages: list) -> tuple[str | None, list[str]]:
    """Detect coding agent phase from recent tool usage in conversation history.

    Returns ("HAS_WRITES" | "READS_ONLY" | None, [tool_names]).
    """
    recent_tools: list[str] = []
    for msg in reversed(messages or []):
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
            if block_type == "tool_use":
                name = (
                    getattr(block, "name", None)
                    if not isinstance(block, dict)
                    else block.get("name")
                )
                if name:
                    recent_tools.append(name)
        if len(recent_tools) >= 5:
            break

    if not recent_tools:
        return None, []
    if any(t in WRITE_TOOLS for t in recent_tools):
        return "HAS_WRITES", recent_tools
    return "READS_ONLY", recent_tools


def _detect_analysis_from_history(messages: list) -> bool:
    """Check if any recent user message in the conversation requested analysis.

    CC sends stateless requests — each turn re-sends the full conversation.
    The last user message may be a tool_result, not the original analysis
    request. Scanning recent user messages detects the analysis session.

    Scans up to 10 user messages (enough to detect active analysis, but NOT
    the entire history — prevents the sticky-forever problem where a single
    analysis request 500 turns ago still triggers analysis routing).
    """
    checked = 0
    for msg in reversed(messages or []):
        role = (
            getattr(msg, "role", None)
            if not isinstance(msg, dict)
            else msg.get("role")
        )
        if role != "user":
            continue
        content = (
            getattr(msg, "content", None)
            if not isinstance(msg, dict)
            else msg.get("content")
        )
        text = content_to_rough_text(content) if not isinstance(content, str) else content
        if is_analysis_request(text):
            return True
        checked += 1
        if checked >= 10:
            break
    return False


def _count_unique_reads(messages: list) -> tuple[int, int]:
    """Count total read tool calls and unique files read across all assistant messages.

    Returns (total_reads, unique_files_count).
    Used to detect diminishing returns: if unique/total ratio is low,
    the agent is re-reading already-seen files → synthesis signal.
    """
    total_reads = 0
    seen_files: set[str] = set()
    for msg in reversed(messages or []):
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
            if name not in ("Read", "Grep", "Glob"):
                continue
            total_reads += 1
            inp = (
                getattr(block, "input", None)
                if not isinstance(block, dict)
                else block.get("input")
            )
            if isinstance(inp, dict):
                file_path = inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
                if file_path:
                    seen_files.add(str(file_path))
    return total_reads, len(seen_files)


def _count_consecutive_reads(messages: list) -> int:
    """Count consecutive read-only assistant responses from the end of history.

    Returns the number of consecutive turns where the assistant only used
    read-type tools (Read, Grep, Glob, etc.) with no writes.
    Used to detect when the agent has done enough reading and should synthesize.
    """
    count = 0
    for msg in reversed(messages or []):
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
        has_tool = False
        has_write = False
        for block in content:
            block_type = (
                getattr(block, "type", None)
                if not isinstance(block, dict)
                else block.get("type")
            )
            if block_type == "tool_use":
                has_tool = True
                name = (
                    getattr(block, "name", None)
                    if not isinstance(block, dict)
                    else block.get("name")
                )
                if name in WRITE_TOOLS:
                    has_write = True
                    break
        if has_write:
            break
        if has_tool:
            count += 1
        if count >= 20:
            break
    return count


class IntentClassifierTransformer(Transformer):
    """Classify user intent (LLM or regex) and detect agent phase.

    Supports 6 intents: READ, PLAN, BUILD, VERIFY, CHAT, SYNTHESIZING.
    SYNTHESIZING is a sub-phase of READ (triggered after many consecutive reads).
    """

    @property
    def name(self) -> str:
        return "intent_classifier"

    def __init__(
        self,
        classifier_cfg: ClassifierConfig,
        policy_cfg: PolicyConfig,
        models_differ: bool,
        synth_reads_fallback: int = 20,
    ) -> None:
        self._cls = classifier_cfg
        self._policy = policy_cfg
        self._models_differ = models_differ
        self._synth_fallback = synth_reads_fallback

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        last_text = get_last_user_text(request.messages)
        messages = getattr(request, "messages", [])

        # Step 1: Detect tool history + analysis state BEFORE classification
        history_phase, tool_names = _detect_phase(messages)
        analysis_detected = is_analysis_request(last_text) or _detect_analysis_from_history(messages)
        # Pure analysis intent: analysis keywords WITHOUT building keywords in same message.
        # Used for Override A bypass (HAS_WRITES pivot) and Override C1.
        # "Fix all bugs exhaustively" → False (both ANALYSIS_RE + BUILDING_RE match)
        # "Lee exhaustivamente todos los archivos" → True (only ANALYSIS_RE matches)
        _is_explicit_analysis = is_analysis_request(last_text) and not bool(BUILDING_RE.search(last_text))
        consecutive_reads = _count_consecutive_reads(messages) if analysis_detected else 0

        # Detect if last user message is a Gather continuation (no new intent).
        # Used for CC Gather-Act-Verify phase detection:
        # - tool_result only → Gather continuation (agent still reading files)
        # - short ambiguous text ("continue", "yes") → inherits session phase
        # - text message with real intent → classifier evaluates by content
        last_msg = next(
            (m for m in reversed(messages or [])
             if (getattr(m, "role", None) if not isinstance(m, dict) else m.get("role")) == "user"),
            None,
        )
        last_content = (
            getattr(last_msg, "content", None) if last_msg and not isinstance(last_msg, dict)
            else (last_msg.get("content") if last_msg else None)
        )
        # Pure tool_result: no user text at all → always a continuation
        _is_tool_result_only = _is_pure_tool_result(last_content)
        # Short ambiguous text: "continue", "yes", "sí", "go on" — carries no intent.
        # Only treated as continuation when in an active analysis session.
        _is_short_ambiguous = (
            bool(last_text)
            and len(last_text.strip()) <= 30
            and not is_analysis_request(last_text)
            and not bool(BUILDING_RE.search(last_text))
        )
        _is_gather_continuation = _is_tool_result_only or (
            _is_short_ambiguous and analysis_detected and history_phase != "HAS_WRITES"
        )

        # Step 2: Build enriched tool_context for the LLM classifier
        tool_context = ""
        if tool_names:
            tool_context = f"Recent tools used: {', '.join(tool_names[:5])}"
            if history_phase == "HAS_WRITES":
                tool_context += " (includes file writes/edits)"
            else:
                tool_context += " (read-only operations so far)"

        # Inject analysis session signal so classifier can return ANALYZING/SYNTHESIZING.
        # Only inject when the CURRENT message justifies it (CC phase-aware):
        # 1. Explicit analysis request → user is entering/continuing Gather phase
        # 2. Pure tool_result in analysis session → Gather continuation (agent still reading)
        # Do NOT inject for messages with new text in analysis sessions — let the
        # classifier evaluate the content to detect CC phase transitions (Gather→Act).
        _inject_analysis_context = (
            _is_explicit_analysis
            or (_is_gather_continuation and analysis_detected and history_phase != "HAS_WRITES")
        )
        if _inject_analysis_context:
            pivot_note = " [User pivoting from building to analysis]" if (
                history_phase == "HAS_WRITES" and _is_explicit_analysis
            ) else ""
            total_reads, unique_files = _count_unique_reads(messages)
            repeat_note = ""
            if total_reads > 0 and unique_files > 0:
                repeat_rate = 1.0 - (unique_files / total_reads)
                if repeat_rate > 0.6:
                    repeat_note = " Last reads cover already-seen files."
            tool_context += (
                f". ANALYSIS SESSION: {consecutive_reads} read turns, "
                f"{unique_files} unique files read.{repeat_note}{pivot_note}"
            )

        # Step 3: Text classification with enriched context (LLM or regex)
        if self._cls.model and self._models_differ:
            ctx.intent = await classify_intent(
                last_text,
                model=self._cls.model,
                api_key=self._cls.api_key,
                api_base=self._cls.base_url,
                timeout_s=self._cls.timeout,
                tool_context=tool_context,
            )
            # Compare LLM vs regex for accuracy validation (doesn't change routing)
            regex_intent = _regex_fallback_intent(last_text)
            # Compare directly without normalization - new intents are distinct
            if ctx.intent != regex_intent:
                metrics.increment_classifier_disagreement()
                metrics.record_model_event(
                    "classifier", f"disagree_{ctx.intent}_vs_{regex_intent}",
                )
                logger.info(
                    "[classify] DISAGREE: llm=%s regex=%s text=%.100s",
                    ctx.intent, regex_intent, last_text,
                )
        else:
            ctx.intent = _regex_fallback_intent(last_text)

        # Step 4: Map intents to analysis_phase and phase
        if ctx.intent == "READ":
            ctx.analysis_phase = "READ"
            ctx.is_analysis = True
            ctx.analysis_read_count = consecutive_reads
            ctx.phase = "PLAN"
        elif ctx.intent == "SYNTHESIZING":
            ctx.analysis_phase = "SYNTHESIZING"
            ctx.is_analysis = True
            ctx.analysis_read_count = consecutive_reads
            ctx.phase = "PLAN"
        else:
            # Normal intents — set analysis_phase from detection state
            if analysis_detected and history_phase == "HAS_WRITES":
                ctx.analysis_phase = "DONE"
            else:
                ctx.analysis_phase = "NONE"
            ctx.is_analysis = False
            ctx.analysis_read_count = consecutive_reads
            # New intent mapping: READ/PLAN → PLAN phase, BUILD/VERIFY → EXECUTE, CHAT → EXPLORE
            ctx.phase = {
                "CHAT": "EXPLORE",
                "READ": "PLAN",
                "PLAN": "PLAN",
                "BUILD": "EXECUTE",
                "VERIFY": "EXECUTE",
                # Legacy intents mapping (for backwards compatibility)
                # Legacy compat (should not reach here, but safe)
                "PLANNING": "PLAN",
                "BUILDING": "EXECUTE",
                "ANALYZING": "PLAN",
                "SYNTHESIZING": "PLAN",
            }.get(ctx.intent, "EXECUTE")

        # Step 5: Post-classification overrides

        # Override A: HAS_WRITES + NOT pure analysis pivot → force BUILDING
        # Does NOT fire for explicit analysis requests — allows build→analysis pivot.
        # FIX: Don't set analysis_phase=DONE when intent changes to BUILD - causes contradictory state
        if history_phase == "HAS_WRITES" and ctx.intent != "BUILD" and not _is_explicit_analysis:
            logger.info(
                "[classify] OVERRIDE A: %s → BUILD (has_writes, no explicit analysis, tools=%d)",
                ctx.intent, len(tool_names),
            )
            ctx.intent = "BUILD"
            ctx.phase = "EXECUTE"
            ctx.analysis_phase = "NONE"
            ctx.is_analysis = False

        # Override B: CHAT + reads + NO active analysis session → BUILD
        # Guard added: if analysis_detected, let Override C handle it
        elif ctx.intent == "CHAT" and history_phase == "READS_ONLY" and len(tool_names) >= 3 and not analysis_detected:
            logger.info(
                "[classify] OVERRIDE B: CHAT → BUILD (reads_only, tools=%d)",
                len(tool_names),
            )
            ctx.intent = "BUILD"
            ctx.phase = "EXECUTE"

        # Override C1: Pure analysis request → READ (bypasses HAS_WRITES)
        # "Lee exhaustivamente todos los archivos" + HAS_WRITES → READ (user pivoting)
        if _is_explicit_analysis and ctx.intent not in ("READ", "SYNTHESIZING"):
            logger.info(
                "[classify] OVERRIDE C1: %s → READ (explicit analysis pivot, history=%s)",
                ctx.intent, history_phase or "none",
            )
            ctx.intent = "READ"
            ctx.analysis_phase = "READ"
            ctx.is_analysis = True
            ctx.analysis_read_count = consecutive_reads
            ctx.phase = "PLAN"

        # Override C2: Gather continuation — ONLY for pure tool_result messages.
        # CC Gather-Act-Verify: when the last message is tool_results (no new user text),
        # the agent is still in Gather phase (reading files). Force READ to continue.
        # If the message has new text, the classifier already evaluated it by content —
        # respect that decision to allow CC phase transitions (Gather→Act/Verify).
        elif (
            analysis_detected
            and ctx.intent not in ("READ", "SYNTHESIZING")
            and history_phase != "HAS_WRITES"
            and _is_gather_continuation  # Only for tool_result continuations
        ):
            logger.info(
                "[classify] OVERRIDE C2: %s → READ (gather continuation, reads=%d)",
                ctx.intent, consecutive_reads,
            )
            ctx.intent = "READ"
            ctx.analysis_phase = "READ"
            ctx.is_analysis = True
            ctx.analysis_read_count = consecutive_reads
            ctx.phase = "PLAN"

        # Override D (safety net): Too many reads without SYNTHESIZING → force it
        if ctx.analysis_phase == "READ" and consecutive_reads >= self._synth_fallback:
            logger.info(
                "[classify] OVERRIDE D: READ → SYNTHESIZING (reads=%d >= %d)",
                consecutive_reads, self._synth_fallback,
            )
            ctx.intent = "SYNTHESIZING"
            ctx.analysis_phase = "SYNTHESIZING"

        # Override E: Wrap-up turn — tool_result only + tools_in=0
        # CC sends tool_result turns with no tool definitions when asking the model to conclude.
        # Building models (e.g. MiniMax-M2.5) can't generate tool_use without tool definitions →
        # return 1-token end_turn. Route to big_model via CHAT intent + PLAN phase.
        # Effect: model_router uses big_model (passthrough), intent_enforcement skips
        # enforcement (CHAT → no prompt injected).
        tools_in = len(getattr(request, "tools", []) or [])
        if _is_tool_result_only and tools_in == 0 and not ctx.is_analysis and history_phase != "HAS_WRITES":
            logger.info(
                "[classify] OVERRIDE E: %s → CHAT/PLAN (tool_result only, tools_in=0 wrap-up turn)",
                ctx.intent,
            )
            ctx.intent = "CHAT"
            ctx.phase = "PLAN"
            ctx.is_analysis = False
            ctx.analysis_phase = "NONE"

        if ctx.is_analysis:
            logger.info("[classify] ANALYSIS phase=%s reads=%d (enforcement=%s)",
                        ctx.analysis_phase, ctx.analysis_read_count, self._policy.analysis_enforcement)

        logger.info(
            "[classify] intent=%s phase=%s analysis=%s history=%s tools=%s",
            ctx.intent, ctx.phase, ctx.analysis_phase,
            history_phase or "none",
            ",".join(tool_names[:3]) if tool_names else "none",
        )
