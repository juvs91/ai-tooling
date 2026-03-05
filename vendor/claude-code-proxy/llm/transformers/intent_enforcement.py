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
        """READ/ANALYZING intent: Must use tools BEFORE concluding."""
        return (
            "[INTENT-ENFORCEMENT] READ/ANALYZING mode active:\n"
            "RULE 1: STOP before analyzing. Execute tool calls FIRST. Read the actual files.\n"
            "RULE 2: Only reference content you have explicitly read. "
            "Never assume file names, paths, or extensions.\n"
            "RULE 3: Every claim requires proof: cite (file.py:line) from a file you ACTUALLY read.\n"
            "RULE 4: If a file does not exist when you try to read it, say \"file not found\" "
            "— do NOT invent content.\n"
            "RULE 5: DO NOT generate analysis conclusions in the same response as your tool calls. "
            "Wait for tool results first."
        )

    def _get_plan_prompt(self) -> str:
        """PLAN intent: Must have structured output."""
        return (
            "[INTENT-ENFORCEMENT] PLAN mode active:\n"
            "Produce a structured implementation plan. Required sections:\n"
            "## Context (why this change is needed)\n"
            "## Approach (your recommended solution)\n"
            "## Steps (numbered, specific, actionable)\n"
            "## Files to Modify (list with line ranges)\n"
            "## Verification (how to test the change)\n"
            "Do NOT write implementation code — this is a plan, not execution."
        )

    def _get_synthesizing_prompt(self) -> str:
        """SYNTHESIZING intent: Should NOT make new tool calls."""
        return (
            "[INTENT-ENFORCEMENT] SYNTHESIZING mode active:\n"
            "You have gathered all necessary information from the codebase. "
            "Now synthesize ONLY from what was collected.\n"
            "RULE 1: DO NOT attempt to call tools — none are available in this phase.\n"
            "RULE 2: Reference specific findings from the analysis phase. "
            "Cite (file.py:line) you read earlier.\n"
            "RULE 3: Structure your output: ## Summary → ## Key Findings → ## Recommendations\n"
            "RULE 4: For each finding, state: what you observed, where you found it, what it means.\n"
            "RULE 5: DO NOT speculate about code you did not read. Mark uncertainties explicitly."
        )

    def _get_building_prompt(self) -> str:
        """BUILDING intent: Must use edit tools."""
        return (
            "[INTENT-ENFORCEMENT] BUILDING mode active:\n"
            "RULE 1: Make the file changes NOW. Do not describe what you will do — just do it.\n"
            "RULE 2: Use Edit tool for modifications. Use Write tool for new files. "
            "Use Bash to verify.\n"
            "RULE 3: Each change must be atomic: read → edit → verify.\n"
            "RULE 4: After each file change, confirm the edit applied correctly "
            "(Read the changed section).\n"
            "RULE 5: If a change requires multiple files, use TodoWrite to track all pending changes."
        )

    def _get_verify_prompt(self) -> str:
        """VERIFY intent: Run tests and validate."""
        return (
            "[INTENT-ENFORCEMENT] VERIFY mode active:\n"
            "RULE 1: Run tests or validation commands using Bash tool.\n"
            "RULE 2: Report actual output — do not describe what you expect to happen.\n"
            "RULE 3: If tests fail, identify the specific failure and root cause.\n"
            "RULE 4: Verify in this order: unit tests → integration tests → smoke test."
        )
