# app/router/llm_router.py
from __future__ import annotations
import asyncio
import json
import os
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
    r"arquitect|diseñ|estrateg|riesg|compar|evalua|analiz|alcance|propuesta|planific|revisar|"
    r"bosquejo|esquema|flujograma|cronograma|hito"
    r")\b",
    re.IGNORECASE,
)

BUILDING_RE = re.compile(
    r"\b("
    # English
    r"implement|patch|diff|refactor|fix|bug|error|stacktrace|test|pytest|docker|compose|"
    r"uvicorn|fastapi|litellm|pip|python|bash|endpoint|schema|regex|deploy|migration|"
    r"migrate|database|sql|auth|security|ci|cd|build|install|upgrade|"
    r"optimize|debug|hotfix|release|monitor|integrate|configure|"
    # Spanish
    r"implementa|arregla|corrige|despliega|migra|construye|instala|actualiza|crea|agrega|"
    r"elimina|modifica|escribe|genera|ejecuta|"
    r"optimiza|depura|monitoreo|integrar|configura"
    r")\b",
    re.IGNORECASE,
)

ANALYSIS_RE = re.compile(
    r"\b("
    # English
    r"analy[zs]e?\b.{0,30}(?:code|proxy|codebase|implementation|project|system|architecture)|"
    r"audit\b.{0,20}(?:code|proxy|codebase)|"
    r"exhaustiv|thorough|comprehensive|"
    r"inspect\b.{0,20}(?:code|implementation)|"
    r"list.{0,20}(?:feature|funcionalid|capabilit|endpoint|function)|"
    r"review.{0,10}(?:code|codebase|implementation)|"
    # Spanish
    r"analiz\w*.{0,30}(?:código|proxy|codebase|implementaci|proyecto|sistema|arquitectura)|"
    r"examin\w*.{0,20}(?:código|proxy|codebase)|"
    r"exhaustiv|lista.{0,20}funcionalidad|"
    r"todas.{0,10}funcionalid|revis\w*.{0,10}(?:código|implementaci)"
    r")",
    re.IGNORECASE,
)


def is_analysis_request(text: str) -> bool:
    """Detect if the user is requesting a code analysis / audit / exhaustive review."""
    return bool(ANALYSIS_RE.search(text))

# --------------- LLM Intent Classifier ---------------

_CLASSIFY_PROMPT = (
    "Classify this user message into exactly ONE category. "
    "Reply with ONLY the category name, nothing else.\n\n"
    "Categories:\n"
    "- PLANNING: analysis, design, architecture, comparison, strategy, review, roadmap, outline, blueprint\n"
    "- BUILDING: write code, fix bug, implement, refactor, test, deploy, patch, debug, optimize\n"
    "- CHAT: questions, conversation, explanation, help, greeting\n\n"
    "Examples:\n"
    "- 'How should I design this?' -> PLANNING\n"
    "- 'Fix the authentication bug' -> BUILDING\n"
    "- 'Can you help me?' -> CHAT\n"
    "- 'Crea un plan de implementacion' -> PLANNING\n"
    "- 'Arregla el error de login' -> BUILDING\n"
    "- 'Como funciona esto?' -> CHAT\n\n"
    "Message: {message}\n\n"
    "Category:"
)

_VALID_INTENTS = {"PLANNING", "BUILDING", "CHAT"}


def _regex_fallback_intent(text: str) -> str:
    """Original regex-based intent detection as fallback."""
    is_planning = bool(PLANNING_RE.search(text))
    is_building = bool(BUILDING_RE.search(text))
    if is_building and not is_planning:
        return "BUILDING"
    if is_planning and not is_building:
        return "PLANNING"
    return "CHAT"


async def classify_intent(
    text: str,
    *,
    model: str,
    api_key: str = "",
    api_base: Optional[str] = None,
    timeout_s: float = float(os.environ.get("CLASSIFIER_TIMEOUT", "5.0")),
) -> str:
    """
    Classify user intent using a cheap LLM call.
    Returns: "PLANNING", "BUILDING", or "CHAT".
    Falls back to regex on any error or timeout.
    """
    if not text or not text.strip():
        return "CHAT"

    # Truncate to keep classifier fast but with enough context
    truncated = text[:1000]
    prompt = _CLASSIFY_PROMPT.format(message=truncated)

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


def get_last_user_text(messages: list[Any]) -> str:
    try:
        for m in reversed(messages or []):
            role = getattr(m, "role", None) if not isinstance(m, dict) else m.get("role")
            if role == "user":
                content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
                return content_to_rough_text(content)[:8000]
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
    if intent == "PLANNING":
        score_big += 3
    elif intent == "BUILDING":
        score_build += 3

    # decisión
    if score_build >= 3:
        return building_model
    if score_big >= 3:
        return big_model
    return small_model
