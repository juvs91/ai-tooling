# app/router/llm_router.py
from __future__ import annotations
import asyncio
import json
import re
from typing import Any, Optional

import litellm
from utils.metrics import metrics

PLANNING_RE = re.compile(
    r"\b("
    # English
    r"plan|planning|checklist|steps|roadmap|design|review|rfc|tradeoff|compare|comparison|"
    r"architect|evaluat|analyz|analys|assess|strateg|priorit|scope|proposal|"
    r"outline|blueprint|mockup|flowchart|timeline|milestone|"
    # Spanish
    r"arquitect|dise[ñn]|estrateg|riesg|compar|evalua|anal[ií][zs]|alcance|propuesta|planific|revisar|"
    r"bosquejo|esquema|flujograma|cronograma|hito"
    r")\b",
    re.IGNORECASE,
)

BUILDING_RE = re.compile(
    r"\b("
    # English (test|pytest removed → they belong in VERIFY_RE. "fix the pytest config" still
    # matches on "fix", "implement test utils" on "implement" — no BUILD scenario needs test|pytest)
    r"implement|patch|diff|refactor|fix|bug|error|stacktrace|docker|compose|"
    r"uvicorn|fastapi|litellm|pip|bash|endpoint|schema|regex|deploy|migration|"
    r"migrate|database|sql|auth|security|ci|cd|build|install|upgrade|"
    r"optimize|debug|hotfix|release|monitor|integrate|configure|"
    # English: read + to + action (to fix, to implement, to build)
    r"read\s+\w+\s+to\s+(?:fix|implement|change|build|create)|"
    # Note: tool_result/tool_use_id removed — these are protocol strings, not user intent.
    # Override A/B handle active-agent detection via tool history instead.
    # Spanish
    r"implementa|arregla|corrige|despliega|migra|construye|instala|actualiza|crea|agrega|"
    r"elimina|modifica|escribe|genera|ejecuta|"
    r"optimiza|depura|monitoreo|integrar|configura|"
    # Spanish: lee + para + action (para arreglar, para implementar)
    r"lee\s+\w+\s+para\s+(?:arreglar|corregir|implementar|fix|change|crear|construir)"
    r")\b",
    re.IGNORECASE,
)

_SYSTEM_REMINDER_RE = re.compile(r'<system-reminder>[\s\S]*?</system-reminder>\s*')

ANALYSIS_RE = re.compile(
    r"\b("
    # English — analysis/review actions
    r"analy[zs]e?\b.{0,30}(?:code|proxy|codebase|implementation|project|system|architecture|"
    r"classifier|router|pipeline|transformer|server|config|module|service|component)|"
    r"audit\b.{0,20}(?:code|proxy|codebase)|"
    r"ex[ha]{1,2}ustiv|thorough|comprehensive|in\s+(?:depth|detail|full)\b|"
    r"inspect\b.{0,20}(?:code|implementation)|"
    r"list.{0,20}(?:feature|funcionalid|capabilit|endpoint|function)|"
    r"review.{0,10}(?:code|codebase|implementation)|"
    r"read\s+(?:all|every|each)\b.{0,20}(?:file|code|script|module)|"
    r"deep\s+(?:think|understand|analysis|dive)|"
    r"think\s+(?:deeply|carefully|through)|"
    # English — solution design (requires deep analysis to design well)
    r"solution\s+design|design\s+(?:a\s+)?solution|"
    # English — read+explain pattern
    r"(?:read|grep|search|find)\b.{0,100}(?:tell\s+me|explain|describe)\b.{0,40}(?:how|what|where|which|why)|"
    # Spanish — analysis/review actions (exhaustiv omitted here — already in English section above)
    r"analiz\w*.{0,30}(?:código|proxy|codebase|implementaci|proyecto|sistema|arquitectura|"
    r"clasificador|router|pipeline|transformer|servidor|configura|módulo|servicio|componente)|"
    r"examin\w*.{0,20}(?:código|proxy|codebase)|"
    r"lista.{0,20}funcionalidad|"
    r"todas.{0,10}funcionalid|revis\w*.{0,10}(?:código|implementaci)|"
    # Spanish — lee patterns: original + flexible (adverb between lee and todo)
    r"lee\s+(?:todo|todos|cada)\b.{0,30}(?:código|archivo|script|\.py|módulo)|"
    r"lee\s+\w+\s+(?:todo|todos|cada)\b.{0,30}(?:código|archivo|script|módulo)|"
    # Spanish — depth phrases (unambiguous standalone)
    r"a\s+profundidad|en\s+profundidad|a\s+fondo|en\s+detalle|con\s+detalle|"
    # Spanish — depth adverbs require code context to avoid false positives
    r"profundamente.{0,40}(?:código|codebase|proxy|implementaci|archivo|sistema|arquitectura)|"
    r"detalladamente.{0,40}(?:código|codebase|proxy|implementaci|archivo|sistema)|"
    # Spanish — noun + depth adjective
    r"(?:análisis|analisis|revisión|revision|estudio|diagnóstico|diagnostico)\s+(?:profund|detallad|exhaust|complet|minucioso)\w*|"
    # Spanish — solution design
    r"diseñ\w+\s+(?:la\s+)?soluci[oó]n|diseño\s+de\s+soluci[oó]n|"
    # Spanish — thinking/understanding
    r"piens\w+\s+(?:profund|bien|cuidados)|"
    r"entendimiento\s+profund|comprensión\s+profund"
    r")",
    re.IGNORECASE,
)

# VERIFY_RE: Test/validation intent (run tests, verify changes, etc.)
VERIFY_RE = re.compile(
    r"\b("
    # English
    r"test|pytest|verify|validate|check\s+(?:if|whether)|run\s+(?:the\s+)?tests?|"
    r"assert|spec|integration|unit\s+test|test\s+suite|coverage|"
    r"validate\s+(?:the\s+)?(?:changes|implementation|fix)|"
    # Spanish
    r"prueba|test|verifica|valida|comprueba|corre\s+(?:(?:el|los)\s+)?tests?|"
    r"ejecuta\s+(?:las\s+)?pruebas|testea|testear"
    r")\b",
    re.IGNORECASE,
)


def is_analysis_request(text: str) -> bool:
    """Detect if the user is requesting a code analysis / audit / exhaustive review."""
    return bool(ANALYSIS_RE.search(text))

# --------------- LLM Intent Classifier ---------------

_CLASSIFY_PROMPT = (
    "Classify this AI coding assistant request into ONE category.\n\n"
    "SESSION CONTEXT:\n{context}\n\n"
    "CATEGORIES:\n"
    "- READ: User requests to read/explain code WITHOUT making changes. Goal is to understand and report. "
    "This is the Gather phase. Example: 'analiza el código', 'lee todos los archivos'.\n"
    "- SYNTHESIZING: The agent has gathered sufficient evidence and is ready to write the final analysis "
    "report. Choose this based on EVIDENCE QUALITY, not turn count: "
    "(a) the context shows recent reads are repeating already-seen files (diminishing returns), OR "
    "(b) the user explicitly requests 'write/synthesize/report the analysis now', OR "
    "(c) the context shows the scope of the task is covered (key files have been read). "
    "Do NOT choose SYNTHESIZING just because many reads happened — if new files are still being "
    "discovered, continue with READ.\n"
    "- PLAN: User requests to design/plan BEFORE implementing. Deep reasoning. "
    "Goal is to create a strategy. Example: 'crea un plan', 'diseña la solución'.\n"
    "- BUILD: User requests to write/fix/change code. Goal is to implement or fix something. "
    "This is the Act phase. Example: 'arregla el bug', 'implementa X', 'fix the auth'.\n"
    "- VERIFY: User requests to test/validate changes. Goal is to verify correctness. "
    "Example: 'corre los tests', 'valida los cambios', 'run the test suite'.\n"
    "- CHAT: Questions, explanations, casual conversation. No tool execution needed.\n\n"
    "DISAMBIGUATION RULES:\n"
    "1. 'lee/analiza + para entender/reportar' → READ\n"
    "2. 'lee/analiza + para arreglar/cambiar/implementar' → BUILD\n"
    "3. 'lee/analiza + para crear un plan/estrategia' → PLAN\n"
    "4. test/pytest/verify/valida + código existente → VERIFY\n"
    "5. HAS_WRITES + tool_result/continue → BUILD\n"
    "6. Question without code context → CHAT\n"
    "7. Context shows last reads cover already-seen files AND task scope is covered → SYNTHESIZING\n"
    "8. 'escribe el análisis/informe/reporte' OR 'now synthesize' → SYNTHESIZING\n\n"
    "EXAMPLES:\n"
    # READ examples
    "- 'lee todos los archivos y explícalos' → READ\n"
    "- 'read and explain the codebase' → READ\n"
    "- 'analiza el proxy' → READ\n"
    "- 'revisa el código' → READ\n"
    # SYNTHESIZING examples
    "- 'ya tienes suficiente, escribe el análisis' → SYNTHESIZING\n"
    "- 'now write the final report' → SYNTHESIZING\n"
    "- tool_result after context shows 'Last reads cover already-seen files' → SYNTHESIZING\n"
    # PLAN examples
    "- 'How should we design the new API?' → PLAN\n"
    "- 'Crea un plan de implementación' → PLAN\n"
    "- 'diseña la solución para este problema' → PLAN\n"
    "- 'créame un roadmap' → PLAN\n"
    # BUILD examples
    "- 'Fix the authentication bug' → BUILD\n"
    "- 'Arregla el error de login' → BUILD\n"
    "- 'Read server.py and fix the bug' → BUILD\n"
    "- 'lee los archivos para corregir el bug' → BUILD\n"
    "- 'implementa la nueva funcionalidad' → BUILD\n"
    # VERIFY examples
    "- 'corre los tests' → VERIFY\n"
    "- 'valida los cambios' → VERIFY\n"
    "- 'run the test suite' → VERIFY\n"
    "- 'pytest tests/' → VERIFY\n"
    # CHAT examples
    "- 'Can you help me?' → CHAT\n"
    "- 'Como funciona esto?' → CHAT\n\n"
    "Message: {message}\n\n"
    "Category:"
)

_VALID_INTENTS = {"PLAN", "BUILD", "CHAT", "READ", "SYNTHESIZING", "VERIFY"}


def _regex_fallback_intent(text: str) -> str:
    """Regex-based intent detection as fallback when LLM classifier times out.

    Priority: READ > BUILD > PLAN > VERIFY (standalone) > CHAT
    VERIFY only wins when no stronger BUILD/PLAN/READ signals are present,
    because \\btest\\b is too broad (matches "design the test architecture").
    """
    building_matches = len(BUILDING_RE.findall(text))
    is_building = building_matches >= 1
    is_analysis = is_analysis_request(text)

    # READ: analysis/review intent - without BUILD signal
    if is_analysis and not is_building:
        return "READ"

    is_planning = bool(PLANNING_RE.search(text))
    is_verify = bool(VERIFY_RE.search(text))

    if is_building and not is_planning:
        return "BUILD"
    if is_planning and not is_building:
        return "PLAN"
    if is_planning and is_building:
        # Ambiguous: both concepts present → prefer PLAN (deep reasoning).
        return "PLAN"

    # VERIFY: only when no BUILD/PLAN/READ signals detected.
    # "run tests" → VERIFY, but "design the test architecture" → PLAN (caught above).
    if is_verify:
        return "VERIFY"

    return "CHAT"


async def classify_intent(
    text: str,
    *,
    model: str,
    api_key: str = "",
    api_base: Optional[str] = None,
    timeout_s: float = 3.0,
    tool_context: str = "",
) -> str:
    """
    Classify user intent using a cheap LLM call.
    Returns: "PLANNING", "BUILDING", or "CHAT".
    Falls back to regex on any error or timeout.
    Tool context (recent tools used) is included in prompt for holistic routing.
    """
    if not text or not text.strip():
        return "CHAT"

    # Truncate to keep classifier fast but with enough context
    truncated = text[:1000]
    context_str = f"Context: {tool_context}\n\n" if tool_context else ""
    prompt = _CLASSIFY_PROMPT.format(message=truncated, context=context_str)

    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 5,
            "temperature": 0.0,
            "stream": False,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base

        resp = await asyncio.wait_for(
            litellm.acompletion(**kwargs),
            timeout=timeout_s,
        )

        raw = (resp.choices[0].message.content or "").strip().upper()
        # Extract first valid intent word
        for word in raw.split():
            cleaned = word.strip(".,;:!?\"'")
            if cleaned in _VALID_INTENTS:
                metrics.classifier_llm_success += 1
                return cleaned

        # LLM responded but not a valid category
        metrics.classifier_regex_fallback += 1
        return _regex_fallback_intent(text)

    except (asyncio.TimeoutError, Exception) as e:
        print(f"[classify_intent] fallback to regex: {type(e).__name__}: {e}")
        metrics.classifier_regex_fallback += 1
        return _regex_fallback_intent(text)

def content_to_rough_text(content: Any) -> str:
    """
    Flatten de content (str o lista de content blocks Anthropic) a texto aproximado.
    NO para logging, solo para heurística de routing.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    out: list[str] = []

    if isinstance(content, list):
        for block in content:
            btype = getattr(block, "type", None) if not isinstance(block, dict) else block.get("type")

            if btype == "text":
                txt = getattr(block, "text", None) if not isinstance(block, dict) else block.get("text")
                if txt:
                    out.append(str(txt))
                continue

            if btype == "tool_result":
                nested = getattr(block, "content", None) if not isinstance(block, dict) else block.get("content")
                if isinstance(nested, str):
                    out.append(nested)
                elif isinstance(nested, list):
                    for nb in nested:
                        nbtype = getattr(nb, "type", None) if not isinstance(nb, dict) else nb.get("type")
                        if nbtype == "text":
                            nt = getattr(nb, "text", None) if not isinstance(nb, dict) else nb.get("text")
                            if nt:
                                out.append(str(nt))
                        else:
                            try:
                                out.append(json.dumps(nb)[:1000])
                            except Exception:
                                out.append(str(nb)[:1000])
                else:
                    try:
                        out.append(json.dumps(nested)[:1000])
                    except Exception:
                        out.append(str(nested)[:1000])
                continue

            # fallback corto para tool_use/image/otros
            try:
                out.append(json.dumps(block)[:500])
            except Exception:
                out.append(str(block)[:500])

        return "\n".join(out).strip()

    try:
        return json.dumps(content)[:2000]
    except Exception:
        return str(content)[:2000]


def _is_pure_tool_result(content: Any) -> bool:
    """Check if a user message content is ONLY tool_result blocks (no text)."""
    if not isinstance(content, list):
        return False
    if not content:
        return False
    for block in content:
        btype = getattr(block, "type", None) if not isinstance(block, dict) else block.get("type")
        if btype != "tool_result":
            return False
    return True


def get_last_user_text(messages: list[Any]) -> str:
    """Get text from the last user message that contains actual user intent.

    Skips pure tool_result messages (which contain file contents, bash output,
    etc.) and finds the most recent user message with actual text. This prevents
    the classifier from seeing code/output and misclassifying intent.

    Falls back to the most recent tool_result if no text message is found
    within the last 10 user messages.
    """
    try:
        fallback = ""
        checked = 0
        for m in reversed(messages or []):
            role = getattr(m, "role", None) if not isinstance(m, dict) else m.get("role")
            if role != "user":
                continue
            content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
            raw = content_to_rough_text(content)
            text = _SYSTEM_REMINDER_RE.sub('', raw).strip()[:8000]
            if not fallback and text:
                fallback = text
            if not _is_pure_tool_result(content):
                return text
            checked += 1
            if checked >= 10:
                break
        return fallback
    except Exception:
        pass
    return ""


def choose_local_model(
    *,
    messages: list[Any],
    max_out: int,
    approx_tokens: int,
    system_chars: int,
    tools_count: int,
    small_model: str,
    big_model: str,
    building_model: str,
    intent: str = "CHAT",
) -> str:
    """
    Determinista: decide SMALL vs BIG vs BUILDING para OLLAMA/LOCAL.
    intent: "PLANNING", "BUILDING", or "CHAT" (from LLM classifier or regex fallback).
    """
    messages_count = len(messages or [])

    score_big = 0
    score_build = 0

    # carga / tamaño
    if messages_count > 10:
        score_big += 2
    if approx_tokens > 6000:
        score_big += 3
    elif approx_tokens > 3500:
        score_big += 1
    if system_chars > 4000:
        score_big += 1

    # salida esperada
    if max_out > 900:
        score_build += 2
        score_big += 1
    elif max_out > 600:
        score_big += 2

    # tools (en local suelen ir off, pero si vienen, pesa)
    if tools_count > 0:
        score_big += 2

    # señales semánticas (from LLM classifier)
    if intent in ("PLAN", "PLANNING"):
        score_big += 3
    elif intent in ("BUILD", "BUILDING"):
        score_build += 3

    # decisión
    if score_build >= 3:
        return building_model
    if score_big >= 3:
        return big_model
    return small_model
