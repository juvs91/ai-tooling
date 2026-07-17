import re
from typing import Any

from llm.pipeline import Transformer, TransformContext
from utils.utils import ensure_system_note

_PLAN_PATTERNS = re.compile(
    r"\bplanea\b"
    r"|haz un plan"
    r"|diseña una solución"
    r"|prop[oó]n un enfoque"
    r"|c[oó]mo abordar[í]as"
    r"|qu[eé] har[í]as"
    r"|dame una propuesta"
    r"|make a plan"
    r"|plan this out"
    r"|design a solution"
    r"|how would you approach"
    r"|what would you do",
    re.IGNORECASE,
)

_PLAN_MODE_ENTER_NOTE = (
    "[PLAN-MODE-REQUIRED] Se detectó intent de planificación explícita. "
    "DEBES llamar EnterPlanMode ANTES de responder con cualquier propuesta o diseño. "
    "No respondas con texto de planificación sin estar en plan mode."
)

_PLAN_MODE_EXIT_NOTE = (
    "[PLAN-MODE-ACTIVE] Estás en plan mode. "
    "PASO 1: Si aún no has llamado EnterPlanMode, llámalo AHORA con input vacío {} "
    "como tu primer tool call antes de explorar el codebase. "
    "PASO 2: Explora, lee archivos, usa AskUserQuestion si necesitas aclaraciones. "
    "PASO 3: Escribe el plan completo en .claude/plans/<nombre>.md con Write tool. "
    "PASO 4: Llama ExitPlanMode con input vacío {} para presentar el plan al usuario. "
    "NUNCA uses ExitWorktree como sustituto de ExitPlanMode — son herramientas distintas."
)


class PlanModeEnforcementTransformer(Transformer):
    @property
    def name(self) -> str:
        return "plan_mode_enforcement"

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        messages = getattr(request, "messages", [])
        if not messages:
            return

        last_msg = messages[-1]
        content = getattr(last_msg, "content", "")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        content_lower = content.lower()

        # Case 1: Explicit planning keyword + not yet in plan mode → inject enter reminder
        if not ctx.plan_mode_active:
            if _PLAN_PATTERNS.search(content_lower):
                ensure_system_note(request, _PLAN_MODE_ENTER_NOTE)
                return

        # Case 2: Already in plan mode → remind to exit when done
        if ctx.plan_mode_active:
            ensure_system_note(request, _PLAN_MODE_EXIT_NOTE)
