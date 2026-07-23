# llm/transformers/intent_classifier.py
from __future__ import annotations

import logging
from typing import Any, NamedTuple

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
from utils.utils import bget
from llm.converters import _system_to_text
from llm.compressor import (
    get_session_plan_mode,
    set_session_plan_mode,
    get_session_plan_mode_source,
    set_session_plan_mode_source,
)
from llm.transformers.deferred_tools import _compute_deferred_session_id


WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})
READ_TOOLS = frozenset({"Read", "Grep", "Glob", "Bash", "WebFetch", "WebSearch"})


class _IntentOverride(NamedTuple):
    """Result returned by _resolve_primary_overrides when an override fires."""
    name: str             # Label for logging (e.g. "A", "G", "C1")
    intent: str           # New intent value
    phase: str            # New phase value
    analysis_phase: str   # New analysis_phase value
    is_analysis: bool     # New is_analysis flag
    analysis_read_count: int  # Pass consecutive_reads through; 0 for non-analysis overrides


def _detect_phase(messages: list) -> tuple[str | None, list[str]]:
    """Detect coding agent phase from tool usage in conversation history.

    Scans ALL assistant messages and returns HAS_WRITES immediately when a Write
    tool is found — no tool-count window. A Write at any point in the conversation
    is evidence of an implementation context regardless of how many reads followed.

    Collects the first 5 tools encountered for tool_context display only.

    Returns ("HAS_WRITES" | "READS_ONLY" | None, [tool_names]).
    """
    recent_tools: list[str] = []  # First 5 tools for tool_context display
    for msg in reversed(messages or []):
        if bget(msg, "role") != "assistant":
            continue
        content = bget(msg, "content")
        if not isinstance(content, list):
            continue
        for block in content:
            if bget(block, "type") == "tool_use":
                name = bget(block, "name")
                if name:
                    if len(recent_tools) < 5:
                        recent_tools.append(name)
                    if name in WRITE_TOOLS:
                        return "HAS_WRITES", recent_tools  # early exit

    if not recent_tools:
        return None, []
    return "READS_ONLY", recent_tools


def _get_last_assistant_tools(messages: list) -> list[str]:
    """Return tool names from the most recent assistant message's tool_use blocks.

    Single-message lookahead (not _detect_phase's 5-message window) — used by
    Override F to determine which tool the agent just called in SYNTHESIZING mode.
    """
    for msg in reversed(messages or []):
        if bget(msg, "role") != "assistant":
            continue
        content = bget(msg, "content")
        if not isinstance(content, list):
            break
        names = []
        for block in content:
            if bget(block, "type") == "tool_use":
                name = bget(block, "name")
                if name:
                    names.append(name)
        return names  # Return on first assistant message found (even if empty)
    return []


def _get_recent_all_tools(messages: list, window: int = 20) -> list[str]:
    """Collect all tool names (incl. workflow tools) from recent assistant messages.

    Unlike _detect_phase, this includes workflow tools like ExitPlanMode, EnterPlanMode
    that are not in WRITE_TOOLS or READ_TOOLS. Used by Override G to detect plan
    boundaries.
    """
    all_tools: list[str] = []
    for msg in reversed(messages or []):
        if bget(msg, "role") != "assistant":
            continue
        content = bget(msg, "content")
        if not isinstance(content, list):
            continue
        for block in content:
            if bget(block, "type") == "tool_use":
                name = bget(block, "name")
                if name:
                    all_tools.append(name)
        if len(all_tools) >= window:
            break
    return all_tools


def _plan_mode_active(messages: list) -> bool:
    """Return True if EnterPlanMode was called without a subsequent ExitPlanMode.

    Duplicated from intent_enforcement._plan_mode_active_from_history() to avoid
    circular imports between sibling transformer modules. Both must stay in sync
    if the scan logic changes. Window=120 matches _exit_plan_already_called() in
    deferred_tools.py.
    Plan sessions with many file reads easily exceed 20 messages; 120 gives
    sufficient coverage for typical planning sessions without relying on session cache.
    """
    recent = messages[-120:] if len(messages) > 120 else messages
    found_enter = False
    for msg in recent:
        if bget(msg, "role") != "assistant":
            continue
        content = bget(msg, "content")
        for block in content or []:
            if bget(block, "type") != "tool_use":
                continue
            name = bget(block, "name", "")
            if name == "EnterPlanMode":
                found_enter = True
            elif name == "ExitPlanMode":
                found_enter = False
    return found_enter


def _resolve_primary_overrides(
    ctx_intent: str,
    history_phase: str | None,
    _is_explicit_analysis: bool,
    analysis_detected: bool,
    _is_gather_continuation: bool,
    tool_names: list[str],
    consecutive_reads: int,
    messages: list,
    plan_mode_active: bool = False,
) -> _IntentOverride | None:
    """Resolve primary intent overrides in explicit priority order.

    Evaluates each override condition in sequence; returns on FIRST match.
    Priority is defined by position — P1 is highest, P5 is lowest.
    Returns None if no override fires (keep classifier result).

    Replaces the fragile A/B elif and C1/C2 elif chains. Adding a new override
    means inserting a new block at the correct priority position — no elif chain
    to break.

    F, D, E (state machine transitions on analysis_phase) are handled separately
    by the caller and are not part of this function.
    """
    # P0: Plan mode lock — highest priority gate.
    # When EnterPlanMode was called without subsequent ExitPlanMode, the model is in
    # an active planning session. Lock intent=PLAN so PLAN enforcement fires (not READ).
    # Blocks C1, C2, A, B from breaking the plan session.
    #
    # Natural transition: _plan_mode_active() returns False once ExitPlanMode is called
    # (found_enter resets to False), so P2/Override G fires correctly on the next turn.
    #
    # Override A (HAS_WRITES) is also blocked: writing the plan .md file sets
    # HAS_WRITES but must NOT route to BUILD enforcement before ExitPlanMode.
    #
    # consecutive_reads is preserved so intent_enforcement can nudge the model
    # when it has been reading for many turns without calling ExitPlanMode.
    if plan_mode_active:
        return _IntentOverride("PLAN_LOCK", "PLAN", "PLAN", "NONE", False, consecutive_reads)

    # P1: Explicit analysis request — user intent unambiguous.
    # Overrides HAS_WRITES and ExitPlanMode. Allows build→analysis pivot.
    if _is_explicit_analysis and ctx_intent not in ("READ", "SYNTHESIZING"):
        return _IntentOverride("C1", "READ", "PLAN", "READ", True, consecutive_reads)

    # P2: ExitPlanMode in recent history — hard planning boundary.
    # Model exited plan mode → next turn MUST be implementation.
    # Window=60 matches _plan_mode_active() / _exit_plan_already_called() so that
    # ExitPlanMode detection is consistent with plan mode detection across long sessions.
    if not _is_explicit_analysis and ctx_intent != "BUILD":
        _recent_all = _get_recent_all_tools(messages, window=120)
        if "ExitPlanMode" in _recent_all:
            return _IntentOverride("G", "BUILD", "EXECUTE", "NONE", False, 0)

    # P3: Write tools anywhere in history — implementation is in progress.
    # Any Write at any point in the conversation is evidence of implementation context.
    if history_phase == "HAS_WRITES" and ctx_intent != "BUILD" and not _is_explicit_analysis:
        return _IntentOverride("A", "BUILD", "EXECUTE", "NONE", False, 0)

    # P4: Gather continuation — analysis mid-stream (tool_result turns only).
    # CC Gather-Act-Verify: continue reading when analysis session is active and
    # the last message is tool_results with no new user text.
    if (analysis_detected
            and ctx_intent not in ("READ", "SYNTHESIZING")
            and history_phase != "HAS_WRITES"
            and _is_gather_continuation):
        return _IntentOverride("C2", "READ", "PLAN", "READ", True, consecutive_reads)

    # P5: CHAT + read activity without analysis — heuristic BUILD detection.
    # Model is using tools but classifier returned CHAT → ongoing coding session.
    if (ctx_intent == "CHAT"
            and history_phase == "READS_ONLY"
            and len(tool_names) >= 3
            and not analysis_detected):
        return _IntentOverride("B", "BUILD", "EXECUTE", "NONE", False, 0)

    return None


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
        if bget(msg, "role") != "user":
            continue
        content = bget(msg, "content")
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
        if bget(msg, "role") != "assistant":
            continue
        content = bget(msg, "content")
        if not isinstance(content, list):
            continue
        for block in content:
            if bget(block, "type") != "tool_use":
                continue
            name = bget(block, "name")
            if name not in ("Read", "Grep", "Glob"):
                continue
            total_reads += 1
            inp = bget(block, "input")
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
        if bget(msg, "role") != "assistant":
            continue
        content = bget(msg, "content")
        if not isinstance(content, list):
            continue
        has_tool = False
        has_write = False
        for block in content:
            if bget(block, "type") == "tool_use":
                has_tool = True
                name = bget(block, "name")
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

        # Clients that don't send X-Session-ID (e.g. Claude Code VS Code extension —
        # confirmed via live proxy logs: session_id="" for an entire real session)
        # would otherwise never persist plan_mode_active across turns, since Signal 3
        # below silently no-ops without a session_id. Fall back to the same
        # deterministic conversation-prefix hash DeferredToolsTransformer already
        # uses, so both transformers agree on the same derived session identity.
        # See ADR-0030.
        effective_sid = ctx.session_id or _compute_deferred_session_id(messages)

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
            (m for m in reversed(messages or []) if bget(m, "role") == "user"),
            None,
        )
        last_content = bget(last_msg, "content") if last_msg else None
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

        # Inject plan session context so the LLM classifier avoids misclassifying
        # natural plan-session language (e.g., "hay un error") as BUILD. ADR-0010.
        _cached_plan_active = await get_session_plan_mode(effective_sid) if effective_sid else False
        if _plan_mode_active(messages) or _cached_plan_active:
            tool_context += " [PLAN SESSION ACTIVE: do NOT classify as BUILD/VERIFY unless ExitPlanMode was already called]"

        # Step 3: Text classification with enriched context (LLM or regex)
        if self._cls.model and self._models_differ:
            _intent, _confidence, _secondary = await classify_intent(
                last_text,
                model=self._cls.model,
                api_key=self._cls.api_key,
                api_base=self._cls.base_url,
                timeout_s=self._cls.timeout,
                tool_context=tool_context,
                max_consecutive_errors=self._cls.max_consecutive_errors,
                circuit_reset_seconds=self._cls.circuit_reset_seconds,
            )
            ctx.intent = _intent
            ctx.intent_confidence = _confidence
            ctx.secondary_intent = _secondary or ""
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

        # Step 5: Post-classification overrides (P0 highest priority, P5 lowest)
        # Compute plan mode once here — ctx.plan_mode_active is the authoritative signal
        # for all downstream transformers (intent_enforcement, deferred_tools, etc.).
        plan_mode_active = _plan_mode_active(messages)
        _system_text = _system_to_text(getattr(request, "system", None))
        _signal1_active = False
        _activation_signal = "0" if plan_mode_active else None  # track which signal activated

        if not plan_mode_active:
            # Signal 1: CC system prompt explicitly says "Plan mode is active".
            # This fires when /plan is used but the user's message text would otherwise
            # classify as CHAT or BUILD (e.g., a short reply mid-plan-session).
            if "Plan mode is active" in _system_text:
                plan_mode_active = True
                _signal1_active = True
                _activation_signal = "1"

        # Ralph mode: Ralph injects PROXY_SESSION_MODE: ralph via --append-system-prompt.
        # No headers, no files — the system prompt is the only in-band channel through CC.
        if "PROXY_SESSION_MODE: ralph" in _system_text:
            ctx.ralph_mode = True
            logger.info("[classify] RALPH_MODE detected via system prompt marker")
        # Pre-compute recent tools — reused by Signal 2 guard and ExitPlanMode override.
        _recent_tools = _get_recent_all_tools(messages, window=120)
        if not plan_mode_active and ctx.intent == "PLAN" and history_phase != "HAS_WRITES":
            # Signal 2: Classifier detected PLAN intent. Activate from the FIRST turn
            # even before EnterPlanMode is called, so the first turn is not unprotected.
            # Guards:
            #   - HAS_WRITES in history → impl session active, Override A should apply.
            #   - ExitPlanMode in recent history → plan ended, "implement the plan" must
            #     not re-enter plan mode even though it matches PLANNING_RE.
            if "ExitPlanMode" not in _recent_tools:
                plan_mode_active = True
                _activation_signal = "2"
        # Signal 3: Session cache — persists plan mode state across history truncation
        # and proxy restarts. Provides unlimited coverage for model-initiated plan mode.
        # Uses effective_sid (X-Session-ID, or a deterministic fallback hashed from the
        # conversation prefix for clients that don't send the header — see ADR-0030).
        if not plan_mode_active and effective_sid:
            plan_mode_active = await get_session_plan_mode(effective_sid)
            if plan_mode_active:
                _activation_signal = "3"
        # ExitPlanMode override: if called in recent history, always force False even if
        # the session cache still says True (stale). This handles the first post-exit turn.
        if plan_mode_active and "ExitPlanMode" in _recent_tools:
            plan_mode_active = False
            _activation_signal = None
        # Signal 4: Implicit ExitPlanMode — CC UI switched from /plan → Autoedit/Bypass.
        # Fires ONLY for CC-initiated plan mode (plan_mode_source == "cc"). For proxy-initiated
        # plan mode ("proxy") this would always fire (Signal 1 is never present), causing
        # false-positive exits and periodic oscillation. See ADR-0008, ADR-0010.
        _pm_source: str | None = None
        if effective_sid:
            _pm_source = await get_session_plan_mode_source(effective_sid)
        if _pm_source is None and "EnterPlanMode" in _recent_tools:
            # No cached source yet (no session_id, or first classification of this
            # session) — infer "cc" from the message history itself, not just the
            # live system banner (_signal1_active only reflects THIS turn's system
            # text). Without this, Signal 4 could never fire for a single-shot
            # classification covering a whole session's history. See ADR-0008.
            _pm_source = "cc"
        if plan_mode_active and "Plan mode is active" not in _system_text and ctx.intent in ("BUILD", "VERIFY"):
            if _pm_source == "cc":
                plan_mode_active = False
                _activation_signal = None
                logger.info(
                    "[classify] P0_UNLOCK: CC not in /plan mode + intent=%s + source=cc → implicit ExitPlanMode (ADR-0008)",
                    ctx.intent,
                )
            else:
                logger.debug(
                    "[classify] Signal 4 blocked: source=%s (proxy-initiated, requires explicit ExitPlanMode — ADR-0010)",
                    _pm_source,
                )

        # ── Source tracking (ADR-0010): set on activation, clear on exit ─────────────────
        # Source ("cc"/"proxy") controls whether Signal 4 can fire in future turns.
        if effective_sid and plan_mode_active:
            if _pm_source is None:
                # Source not in cache (new session or proxy restart). Infer from this turn:
                # Signal 1 active → CC-initiated; anything else → proxy-initiated.
                _pm_source = "cc" if _signal1_active else "proxy"
                await set_session_plan_mode_source(effective_sid, _pm_source)
                logger.debug("[classify] plan_mode_source inferred as '%s' (cache was empty)", _pm_source)
            elif _signal1_active and _pm_source != "cc":
                # Signal 1 observed on a session previously marked "proxy" — upgrade to "cc".
                await set_session_plan_mode_source(effective_sid, "cc")
                _pm_source = "cc"

        # Persist final state every turn: sets on activation (with source), clears on exit.
        ctx.plan_mode_active = plan_mode_active
        if _pm_source:
            ctx.plan_mode_source = _pm_source
        if effective_sid:
            _source_to_persist = _pm_source if plan_mode_active else None
            await set_session_plan_mode(
                effective_sid,
                plan_mode_active,
                source=_source_to_persist,
                signal=_activation_signal or "exit",
            )

        # _resolve_primary_overrides() evaluates all conditions in explicit order
        # and returns on first match — no hidden elif coupling.
        _override = _resolve_primary_overrides(
            ctx.intent, history_phase, _is_explicit_analysis,
            analysis_detected, _is_gather_continuation, tool_names,
            consecutive_reads, messages,
            plan_mode_active=plan_mode_active,
        )
        if _override:
            logger.info(
                "[classify] OVERRIDE %s: %s → %s (phase=%s, analysis_phase=%s)",
                _override.name, ctx.intent, _override.intent,
                _override.phase, _override.analysis_phase,
            )
            ctx.intent = _override.intent
            ctx.phase = _override.phase
            ctx.analysis_phase = _override.analysis_phase
            ctx.is_analysis = _override.is_analysis
            ctx.analysis_read_count = _override.analysis_read_count

        # Override F: Agent escaped SYNTHESIZING by calling domain tools on previous turn.
        # If the last assistant message contains READ or WRITE tools, the agent is still
        # actively working — reset to the appropriate phase instead of staying stuck.
        # Workflow tools (TodoWrite, EnterPlanMode, etc.) are NOT in READ_TOOLS/WRITE_TOOLS
        # and are therefore invisible to this override (no phase change).
        if ctx.analysis_phase == "SYNTHESIZING":
            last_tools = _get_last_assistant_tools(messages)
            domain_tools = [t for t in last_tools if t in READ_TOOLS or t in WRITE_TOOLS]
            if domain_tools:
                if any(t in WRITE_TOOLS for t in domain_tools):
                    logger.info(
                        "[classify] OVERRIDE F: SYNTHESIZING → BUILD (tool=%s)", domain_tools
                    )
                    ctx.intent = "BUILD"
                    ctx.analysis_phase = "NONE"
                else:
                    logger.info(
                        "[classify] OVERRIDE F: SYNTHESIZING → READ (tool=%s)", domain_tools
                    )
                    ctx.intent = "READ"
                    ctx.analysis_phase = "READ"
                # Do NOT reset consecutive_reads — natural threshold still applies

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
