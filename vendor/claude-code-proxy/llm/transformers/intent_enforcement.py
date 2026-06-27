"""
Intent Enforcement Transformer

Injects intent-specific prompts to ENFORCE compliance before request.

This is a PRE-FLIGHT transformer that injects system prompts to guide
the model toward proper intent fulfillment.
"""
from __future__ import annotations

import glob as _glob
import logging
import os
import time

from llm.pipeline import Transformer, TransformContext
from utils.utils import ensure_system_note, bget
from llm.converters import _system_to_text

logger = logging.getLogger(__name__)


def _plan_mode_active_from_history(messages: list) -> bool:
    """
    Detect plan mode from conversation history when CC's 'Plan mode is active'
    system reminder is absent.

    Returns True if EnterPlanMode was called in the recent window WITHOUT a
    subsequent ExitPlanMode. This handles the case where CC failed to inject
    its system reminder on the current turn.
    """
    recent = messages[-60:] if len(messages) > 60 else messages
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
                found_enter = False  # plan mode ended
    return found_enter


def _recent_plan_file_exists(max_age_seconds: int = 3600) -> bool:
    """
    Return True if a plan file was written recently in ~/.claude/plans/.
    Used as a last-resort signal: if a plan file exists on disk, a planning
    session is (or was recently) active, even if system state was lost.
    """
    plans_dir = os.path.expanduser("~/.claude/plans/")
    if not os.path.isdir(plans_dir):
        return False
    now = time.time()
    return any(
        now - os.path.getmtime(p) < max_age_seconds
        for p in _glob.glob(os.path.join(plans_dir, "*.md"))
    )


class IntentEnforcementTransformer(Transformer):
    """
    Injects intent-specific system prompts to enforce compliance.

    Uses ensure_system_note() like other transformers (GuardrailTransformer)
    to inject into request.system, not request.messages.
    """

    @property
    def name(self) -> str:
        return "intent_enforcement"

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    async def transform(self, request: object, ctx: TransformContext) -> None:
        """
        Inject intent-specific enforcement prompt into request.system.

        This operates PRE-FLIGHT (before sending to model) by injecting
        system prompts that guide the model toward proper intent fulfillment.
        """
        if not self.enabled:
            return

        intent = ctx.intent
        if not intent or intent == "CHAT":
            return

        # Skip BUILD/VERIFY enforcement when no tool definitions are present.
        # Wrap-up turns (CC asking model to conclude after bash execution) have tools_in=0.
        # Injecting "Make file changes NOW" into a summary turn causes unnecessary tool calls.
        tools_in = len(getattr(request, "tools", []) or [])
        if intent in ("BUILD", "VERIFY") and tools_in == 0:
            return

        # Ralph mode: suppress AskUserQuestion — no human is present in autonomous loops.
        # Injected as a separate note so it stacks with intent-specific prompts.
        if ctx.ralph_mode:
            ensure_system_note(
                request,
                "[RALPH MODE — AUTONOMOUS]: No human is present. "
                "Do NOT call AskUserQuestion under any circumstances. "
                "Make best-effort decisions based on available context. "
                "Document assumptions in the plan file instead of asking. "
                "Prefer ExitPlanMode over asking for clarification.",
            )

        # Adaptive quality enforcement (Item 4) — reads session history to escalate
        # when the model has consistently produced low-quality or stub-heavy responses.
        # Zero coupling: proxy reads its own SessionCache, no external coordination needed.
        session_id = getattr(ctx, "session_id", None)
        if session_id:
            try:
                from llm.compressor import get_session_quality_history
                scores, stub_count = await get_session_quality_history(session_id)
                if scores:
                    avg_quality = sum(scores[-5:]) / len(scores[-5:])
                    adaptive_notes: list[str] = []
                    if avg_quality < 0.55:
                        adaptive_notes.append(
                            "CRITICAL: Your previous responses in this session had quality scores "
                            f"averaging {avg_quality:.0%}. Be extremely thorough, specific, and "
                            "cite (file:line) for every claim."
                        )
                    if stub_count >= 2:
                        adaptive_notes.append(
                            f"STRICT: You have produced {stub_count} stub implementation(s) in this "
                            "session. ALL function bodies MUST contain complete, working logic — "
                            "no `pass`, no `...`, no `# TODO`. Implement fully or not at all."
                        )
                    if adaptive_notes:
                        ensure_system_note(request, "[SESSION-QUALITY] " + " | ".join(adaptive_notes))
                        logger.info(
                            "[adaptive-quality] session=%s avg=%.2f stubs=%d — escalated enforcement",
                            session_id[:8], avg_quality, stub_count,
                        )
            except Exception as exc:
                logger.debug("[adaptive-quality] session history unavailable: %s", exc)

        # Inject intent-specific prompt into request.system
        prompt = self._get_enforcement_prompt(intent, ctx, request=request)
        if prompt:
            ensure_system_note(request, prompt)

    def _get_enforcement_prompt(self, intent: str, ctx: TransformContext, request=None) -> str:
        """Get intent-specific enforcement prompt."""
        if intent == "READ" or intent == "ANALYZING" or ctx.analysis_phase in ("ANALYZING", "READ"):
            return self._get_read_prompt()
        elif intent == "PLAN":
            return self._get_plan_prompt(ctx=ctx, request=request)
        elif intent == "SYNTHESIZING":
            return self._get_synthesizing_prompt()
        elif intent == "BUILDING" or intent == "BUILD":
            return self._get_building_prompt(request=request, ctx=ctx)
        elif intent == "VERIFY":
            return self._get_verify_prompt()
        return ""

    def _get_read_prompt(self) -> str:
        """READ/ANALYZING intent: Grounding requirements with code snippet verification."""
        return (
            "[INTENT-ENFORCEMENT] READ/ANALYZING mode active:\n"
            "RULE 0 — ANTI-REREAD (non-negotiable): If you called Read on the same file "
            "in the last 3 turns, DO NOT read it again. You already have that content. "
            "Reading the same file repeatedly is a loop — break it and move on to the next file.\n"
            "RULE 1: STOP before analyzing. Execute tool calls FIRST. Read the actual files.\n"
            "RULE 2: Only reference content you have explicitly read. "
            "Never assume file names, paths, or extensions.\n"
            "RULE 3: EVERY claim must have a citation: cite (file.py:line) from a file you ACTUALLY read.\n"
            "RULE 4: When making a claim about code behavior, QUOTE the exact code snippet that supports it.\n"
            "RULE 5: Format code citations as: (file.py:line) with the actual code inline.\n"
            "RULE 6: DO NOT generate analysis conclusions in the same response as your tool calls. "
            "Wait for tool results first.\n"
            "GROUNDING RULE: Citations must point to ACTUAL file:line pairs from files you've read. "
            "No citation = no claim. Unverified content = hallucination.\n"
            "CODE VERIFICATION: Include relevant code snippets in your analysis. "
            "For example: 'The function validateToken() checks if the token is expired (auth.py:42):\n"
            '```python\nif token.expiry < now:\n    raise InvalidTokenError\n```\n'
            "PERSISTENCE RULE (ADR-0006): If this is a significant analysis turn, "
            "save your findings to ai-notes/{name}.md using the Write tool. "
            "Chat text is lost on restart — Write tool is the only durable output."
        )

    _PLAN_NUDGE_THRESHOLD = 8  # nudge after this many consecutive read turns during plan mode

    def _get_plan_prompt(self, ctx=None, request=None) -> str:
        """PLAN intent: Grounding requirements for implementation planning."""
        messages = list(getattr(request, "messages", None) or []) if request else []
        system_text = _system_to_text(getattr(request, "system", None)) if request else ""

        # Evaluate all three plan-mode-active signals from the proxy side.
        # The model will also check its own context, but these proxy-side signals
        # handle the case where CC's 'Plan mode is active' reminder is missing.
        # Use ctx.plan_mode_active (authoritative, computed by intent_classifier) when
        # available; fall back to local detection only if ctx is not provided.
        signal_cc = "Plan mode is active" in system_text
        signal_history = _plan_mode_active_from_history(messages)
        signal_disk = _recent_plan_file_exists()
        if ctx is not None:
            plan_mode_active = ctx.plan_mode_active
        else:
            plan_mode_active = signal_cc or signal_history or signal_disk

        # Build proxy note so the model knows what the proxy detected
        proxy_signals: list[str] = []
        if signal_cc:
            proxy_signals.append("SIGNAL 1 (system prompt): TRUE")
        if signal_history:
            proxy_signals.append("SIGNAL 2 (conversation history): EnterPlanMode found without ExitPlanMode → TRUE")
        if signal_disk:
            proxy_signals.append("SIGNAL 3 (disk): recent plan file found in ~/.claude/plans/ → TRUE")
        if not proxy_signals:
            proxy_signals.append("SIGNAL 1/2/3: all FALSE — plan mode not yet active")

        proxy_note = "  [PROXY DETECTION]: " + " | ".join(proxy_signals)

        plan_active_rules = (
            "  ABSOLUTE RULE: Only write/edit the plan file "
            "(.md file under .claude/plans/). "
            "DO NOT call Edit or Write on ANY other file — not source code, "
            "not config files, not scripts, not tests.\n"
            "  FORBIDDEN: editing source files, running code, "
            "executing bash commands that modify files, calling Bash to install, "
            "build, or run anything.\n"
            "  ALLOWED: Read, Glob, Grep, Bash (read-only: ls/cat/git log), "
            "Write/Edit on the plan file only, "
            "ExitPlanMode, AskUserQuestion, TodoWrite.\n"
            "  Produce a structured implementation plan. Required sections:\n"
            "  ## Context (why this change is needed) — cite evidence from codebase\n"
            "  ## Approach (your recommended solution) — reference existing patterns\n"
            "  ## Steps (numbered, specific, actionable) — include file:line references\n"
            "  ## Files to Modify (list with line ranges) — must have read these files\n"
            "  ## Verification (how to test the change) — cite test files and methods\n"
            "  Write your plan to the plan file. Call ExitPlanMode({}) when done.\n"
            "GROUNDING RULE: Every plan section must cite (file.py:line) from files "
            "you have read. Unverified plans will be rejected."
        )

        # Nudge the model when it has been reading for many turns without calling
        # ExitPlanMode. Uses ctx.analysis_read_count (preserved through PLAN_LOCK override).
        # Fires well before Override D's generic SYNTHESIZING threshold (default 20 reads).
        nudge = ""
        if (plan_mode_active
                and ctx is not None
                and ctx.analysis_read_count >= self._PLAN_NUDGE_THRESHOLD):
            nudge = (
                f"\n⚠️ PLAN MODE NUDGE: You have completed {ctx.analysis_read_count} read "
                "turns without calling ExitPlanMode. If you have enough information to write "
                "a complete plan, call ExitPlanMode({}) NOW. "
                "Only continue reading if you are missing critical information you cannot infer."
            )

        if plan_mode_active:
            return (
                "[INTENT-ENFORCEMENT] PLAN mode:\n"
                "CHECK YOUR CONTEXT — verify these signals (in order):\n"
                "  SIGNAL 1: Does the system/context say 'Plan mode is active'?\n"
                "  SIGNAL 2: Is EnterPlanMode in recent assistant messages without ExitPlanMode?\n"
                "  SIGNAL 3: Has a plan file path (~/.claude/plans/*.md) been mentioned?\n"
                f"{proxy_note}\n"
                "CONCLUSION: Plan mode is ACTIVE. Follow the rules below.\n\n"
                + plan_active_rules
                + nudge
            )
        else:
            return (
                "[INTENT-ENFORCEMENT] PLAN mode:\n"
                "CHECK YOUR CONTEXT — verify these signals (in order):\n"
                "  SIGNAL 1: Does the system/context say 'Plan mode is active'?\n"
                "  SIGNAL 2: Is EnterPlanMode in recent assistant messages without ExitPlanMode?\n"
                "  SIGNAL 3: Has a plan file path (~/.claude/plans/*.md) been mentioned?\n"
                f"{proxy_note}\n"
                "If ANY signal is TRUE → plan mode is active, follow the rules below.\n"
                "If ALL signals are FALSE:\n"
                "  ACTION REQUIRED: Call EnterPlanMode with empty input {} as your "
                "FIRST and ONLY tool call. Do nothing else yet.\n"
                "  (Claude Code will activate the Plans tab and send the next turn.)\n\n"
                "WHEN PLAN MODE IS ACTIVE:\n"
                + plan_active_rules
            )

    def _get_synthesizing_prompt(self) -> str:
        """SYNTHESIZING intent: Grounding requirements for synthesis with code verification."""
        return (
            "[INTENT-ENFORCEMENT] SYNTHESIZING mode active:\n"
            "You have gathered significant information from the codebase. "
            "Now begin writing your comprehensive synthesis.\n"
            "MANDATORY (ADR-0006): Use the Write tool to save your analysis to "
            "ai-notes/{descriptive-name}.md BEFORE this turn ends. "
            "Inline text is lost on session restart — only Write tool persists.\n"
            "RULE 1: PRIORITY is producing a written analysis — call Write tool NOW.\n"
            "RULE 2: EVERY claim must cite (file.py:line) from files you've read.\n"
            "RULE 3: Include code snippets to support complex claims about behavior.\n"
            "RULE 4: Structure your output: ## Summary → ## Key Findings → ## Recommendations\n"
            "RULE 5: For each finding, state: what you observed, where you found it, "
            "what it means, with code evidence.\n"
            "RULE 6: DO NOT speculate about code you did not read. Mark uncertainties explicitly.\n"
            "RULE 7: If you need to verify a specific claim before citing it, "
            "you may use tools — but minimize tool calls.\n"
            "GROUNDING RULE: Synthesis must be grounded in verified evidence. "
            "Include code snippets for any behavioral claims."
        )

    def _get_building_prompt(self, request=None, ctx=None) -> str:
        """BUILDING intent: Grounding requirements for code changes + anti-stub mandate."""
        base = (
            "[INTENT-ENFORCEMENT] BUILDING mode active:\n"
            "RULE 1: Make the file changes NOW. Do not describe what you will do — just do it.\n"
            "RULE 2: Use Edit tool for modifications. Use Write tool for new files. "
            "Use Bash to verify.\n"
            "RULE 3: Each change must be atomic: read → edit → verify.\n"
            "RULE 4: After each file change, confirm the edit applied correctly "
            "(Read the changed section).\n"
            "RULE 5: If you're adding a new function, cite where it's called from (caller.py:line).\n"
            "RULE 6: If you're modifying an existing function, explain the change with "
            "before/after code snippets.\n"
            "RULE 7: If a change requires multiple files, use TodoWrite to track all pending changes.\n"
            "GROUNDING RULE: Verify changes by reading the modified code. "
            "Cite the lines you changed.\n"
            "\n"
            "ANTI-STUB MANDATE (non-negotiable):\n"
            "  FORBIDDEN: `pass`, `...`, `# TODO`, `# FIXME`, `raise NotImplementedError`\n"
            "  FORBIDDEN: Empty test bodies — every test must have real assertions\n"
            "  REQUIRED: Every function body must contain complete, working logic\n"
            "  REQUIRED: Every Pydantic model must define all fields with types\n"
            "  REQUIRED: Every API endpoint handler must implement the full request/response flow\n"
            "\n"
            "COMPLETION CHECKLIST — verify before marking done:\n"
            "  [ ] All functions have real implementations (no pass/...)\n"
            "  [ ] All Pydantic schemas define every field\n"
            "  [ ] All tests contain at least one assert statement\n"
            "  [ ] All API route handlers process input and return a response\n"
        )
        # Anti-plan-oscillation: when file changes already exist in history, the model
        # must NOT call EnterPlanMode — it would revert the entire workflow to plan mode
        # and discard in-progress implementation work.
        has_writes = (
            ctx is not None
            and getattr(ctx, "history_phase", None) == "HAS_WRITES"
        )
        anti_plan = ""
        if has_writes:
            anti_plan = (
                "\nRULE 8 [ANTI-PLAN-OSCILLATION]: File changes are already recorded in "
                "this session's history. You are in active BUILD execution — DO NOT call "
                "EnterPlanMode. That tool switches the entire workflow back to planning mode "
                "and interrupts your implementation. If you hit an obstacle, resolve it "
                "inline or call AskUserQuestion. Continue building.\n"
            )
        # RULE 9: Read before Edit (nuanced — skip if you read/wrote the file this same turn)
        rule_read = (
            "\nRULE 9 [READ-BEFORE-EDIT]: Before constructing the old_string for Edit/MultiEdit, "
            "Read the target file UNLESS you read or wrote it in this same turn. "
            "Context compression silently alters what you 'remember' a file contains — "
            "old_string from memory will fail if the file differs. When in doubt: Read first.\n"
        )
        # RULE 10: Task state persistence — model writes to ai-notes/ (proxy has no volume mount)
        rule_state = (
            "\nRULE 10 [TASK-STATE-FILE]: When you complete a phase or mark 3+ tasks done "
            "in TodoWrite, append your current state to ai-notes/task-state-$(date +%Y%m%d).md:\n"
            "cat >> ai-notes/task-state-$(date +%Y%m%d).md << CHECKPOINT\n"
            ">>>>>>>>$(date -u +%Y-%m-%dT%H:%M:%SZ)>>>>>>>>\n"
            "Done: [completed tasks]\n"
            "Current: [active task]\n"
            "Pending: [remaining]\n"
            "Files edited: [list]\n"
            ">>>>>>>>END>>>>>>>>\n"
            "CHECKPOINT\n"
            "If context seems compressed or progress is unclear, Read that file first.\n"
        )
        task_prompt = self._detect_task_type_sub_prompt(request)
        return base + anti_plan + rule_read + rule_state + (task_prompt if task_prompt else "")

    def _detect_task_type_sub_prompt(self, request=None) -> str:
        """Detect task type from recent messages and return specialized sub-prompt.

        Scans the last 10 messages for domain keywords (alembic, fastapi, pytest)
        and injects targeted checklists. Keeps injected content short to avoid
        context bloat — this is advisory, not a replacement for SKILL.md.
        """
        if request is None:
            return ""
        messages = list(getattr(request, "messages", None) or [])
        # Collect text from the last 10 messages (user + assistant)
        recent_text = ""
        for msg in messages[-10:]:
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            if isinstance(content, str):
                recent_text += " " + content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        recent_text += " " + (block.get("text") or "")
        recent_lower = recent_text.lower()

        parts: list[str] = []

        # Database / Alembic / SQLAlchemy
        if any(kw in recent_lower for kw in ("alembic", "migration", "sqlalchemy", "db model", "orm model")):
            parts.append(
                "DATABASE TASK DETECTED — mandatory steps:\n"
                "  1. After creating/modifying SQLAlchemy models: run `alembic revision --autogenerate -m 'desc'`\n"
                "  2. Apply migration: `alembic upgrade head`\n"
                "  3. Verify: `alembic current` (must show the new revision)\n"
                "  4. Define Pydantic schemas (Create/Update/Response) for ALL new models\n"
            )

        # FastAPI / routers / endpoints
        if any(kw in recent_lower for kw in ("router", "fastapi", "endpoint", "api route", "include_router")):
            parts.append(
                "API TASK DETECTED — mandatory steps:\n"
                "  1. Define Pydantic request/response schemas BEFORE writing endpoint handlers\n"
                "  2. Every endpoint handler must be fully implemented — no `pass` bodies\n"
                "  3. Include HTTPException for 4xx errors; handle 5xx with generic handler\n"
                "  4. Register router with `app.include_router(...)` in the main app file\n"
            )

        # Pytest / tests
        if any(kw in recent_lower for kw in ("pytest", "test_", "unittest", "assert ", "fixture")):
            parts.append(
                "TEST TASK DETECTED — mandatory steps:\n"
                "  1. Every test function must contain at least one `assert` statement\n"
                "  2. No `pass` test bodies — implement all test cases fully\n"
                "  3. Run `pytest -x` after writing tests and report actual output\n"
                "  4. If tests fail, fix the implementation before moving on\n"
            )

        return "\n" + "\n".join(parts) if parts else ""

    def _get_verify_prompt(self) -> str:
        """VERIFY intent: Grounding requirements for testing."""
        return (
            "[INTENT-ENFORCEMENT] VERIFY mode active:\n"
            "RULE 1: Run tests or validation commands using Bash tool.\n"
            "RULE 2: Report actual output — do not describe what you expect to happen.\n"
            "RULE 3: If tests fail, identify the specific failure and root cause.\n"
            "RULE 4: Cite the failing test file and test method (test_file.py:line).\n"
            "RULE 5: Verify in this order: unit tests → integration tests → smoke test.\n"
            "GROUNDING RULE: All verification claims must cite actual test results. "
            "Include test output as evidence."
        )
