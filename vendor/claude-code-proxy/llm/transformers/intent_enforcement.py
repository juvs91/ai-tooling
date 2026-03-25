"""
Intent Enforcement Transformer

Injects intent-specific prompts to ENFORCE compliance before request.

This is a PRE-FLIGHT transformer that injects system prompts to guide
the model toward proper intent fulfillment.
"""
from __future__ import annotations

from llm.pipeline import Transformer, TransformContext
from utils.utils import ensure_system_note


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

        # Inject intent-specific prompt into request.system
        prompt = self._get_enforcement_prompt(intent, ctx)
        if prompt:
            ensure_system_note(request, prompt)

    def _get_enforcement_prompt(self, intent: str, ctx: TransformContext) -> str:
        """Get intent-specific enforcement prompt."""
        if intent == "READ" or intent == "ANALYZING" or ctx.analysis_phase in ("ANALYZING", "READ"):
            return self._get_read_prompt()
        elif intent == "PLAN":
            return self._get_plan_prompt()
        elif intent == "SYNTHESIZING":
            return self._get_synthesizing_prompt()
        elif intent == "BUILDING" or intent == "BUILD":
            return self._get_building_prompt()
        elif intent == "VERIFY":
            return self._get_verify_prompt()
        return ""

    def _get_read_prompt(self) -> str:
        """READ/ANALYZING intent: Grounding requirements with code snippet verification."""
        return (
            "[INTENT-ENFORCEMENT] READ/ANALYZING mode active:\n"
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
            '```python\nif token.expiry < now:\n    raise InvalidTokenError\n```'
        )

    def _get_plan_prompt(self) -> str:
        """PLAN intent: Grounding requirements for implementation planning."""
        return (
            "[INTENT-ENFORCEMENT] PLAN mode active:\n"
            "ABSOLUTE RULE: You are ONLY allowed to write/edit the plan file "
            "(a .md file under .claude/plans/). "
            "DO NOT call Edit or Write on ANY other file — not source code, "
            "not config files, not scripts, not tests.\n"
            "FORBIDDEN during plan mode: editing source files, running code, "
            "executing bash commands that modify files, calling Bash to install, "
            "build, or run anything.\n"
            "ALLOWED during plan mode: Read, Glob, Grep, Bash (read-only: ls/cat/git log), "
            "Write/Edit on the plan file only, "
            "EnterPlanMode, ExitPlanMode, AskUserQuestion, TodoWrite.\n"
            "Produce a structured implementation plan. Required sections:\n"
            "## Context (why this change is needed) — cite evidence from codebase\n"
            "## Approach (your recommended solution) — reference existing patterns\n"
            "## Steps (numbered, specific, actionable) — include file:line references\n"
            "## Files to Modify (list with line ranges) — must have read these files\n"
            "## Verification (how to test the change) — cite test files and methods\n"
            "Write your plan to the plan file. Call ExitPlanMode when done.\n"
            "GROUNDING RULE: Every section must cite (file.py:line) from files you have read. "
            "Unverified plans will be rejected."
        )

    def _get_synthesizing_prompt(self) -> str:
        """SYNTHESIZING intent: Grounding requirements for synthesis with code verification."""
        return (
            "[INTENT-ENFORCEMENT] SYNTHESIZING mode active:\n"
            "You have gathered significant information from the codebase. "
            "Now begin writing your comprehensive synthesis.\n"
            "RULE 1: PRIORITY is producing a written analysis — start writing now.\n"
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

    def _get_building_prompt(self) -> str:
        """BUILDING intent: Grounding requirements for code changes."""
        return (
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
            "Cite the lines you changed."
        )

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
