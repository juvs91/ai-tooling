# app/utils/utils.py
from __future__ import annotations
from typing import Any, Optional, Set, Tuple

def parse_allowlist(raw: str) -> Set[str]:
    """
    Parse tool allowlist from comma-separated string.

    Special values:
    - Empty string or not set: No tools allowed (all dropped)
    - "*": All tools allowed (wildcard)
    - "tool1,tool2": Only specified tools allowed

    Returns:
    - Empty set: drop all tools
    - {"*"}: allow all tools (wildcard)
    - {"tool1", "tool2"}: allow only specified tools
    """
    raw = (raw or "").strip()
    if not raw:
        return set()
    # Support wildcard to allow all tools
    if raw == "*":
        return {"*"}
    return {x.strip().lower() for x in raw.split(",") if x.strip()}

def approx_tokens_from_bytes(b: bytes) -> int:
    # heurística rápida (6 bytes ~ 1 token aprox)
    return max(1, len(b) // 6)

def ensure_system_note(request_obj: Any, note: str, system_content_cls: Any = None) -> None:
    """
    Inserta `note` en request.system (str o lista de bloques), dedupe.
    `system_content_cls` = tu SystemContent pydantic si quieres insertar como objeto.
    """
    if not note:
        return

    existing = ""
    sysv = getattr(request_obj, "system", None)

    if sysv is None:
        setattr(request_obj, "system", note)
        return

    if isinstance(sysv, str):
        existing = sysv
        if note in existing:
            return
        setattr(request_obj, "system", note + "\n\n" + sysv)
        return

    if isinstance(sysv, list):
        # intenta extraer texto para dedupe
        parts = []
        for b in sysv:
            if hasattr(b, "text"):
                parts.append(b.text)
            elif isinstance(b, dict):
                parts.append(b.get("text", ""))
        existing = "\n".join(parts)
        if note in existing:
            return

        if system_content_cls is not None:
            sysv.insert(0, system_content_cls(type="text", text=note))
        else:
            sysv.insert(0, {"type": "text", "text": note})
        setattr(request_obj, "system", sysv)

def filter_tools_allowlist(tools: Optional[list[Any]], allow: Set[str]) -> tuple[Optional[list[Any]], list[str]]:
    """
    Filter tools based on allowlist.

    Args:
        tools: List of tools from request
        allow: Set of allowed tool names (or {"*"} for all)

    Returns:
        Tuple of (kept_tools, dropped_tool_names)
    """
    if not tools or not allow:
        return tools, []
    # Wildcard: allow all tools
    if "*" in allow:
        return tools, []
    kept = []
    dropped = []
    for t in tools:
        name = getattr(t, "name", None) if not isinstance(t, dict) else t.get("name")
        name = (name or "").strip()
        if name.lower() in allow:
            kept.append(t)
        else:
            dropped.append(name)
    return kept, dropped

def normalize_tool_choice(tool_choice: Optional[dict], kept_tools: Optional[list[Any]]):
    if not tool_choice:
        return None

    kept_names = set()
    for t in (kept_tools or []):
        name = getattr(t, "name", None) if not isinstance(t, dict) else t.get("name")
        if name:
            kept_names.add(name.lower())

    if not kept_names:
        return None

    ctype = tool_choice.get("type")
    if ctype == "tool":
        name = tool_choice.get("name")
        if name and name.lower() not in kept_names:
            return {"type": "auto"}
        return tool_choice

    if ctype in ("auto", "any"):
        return tool_choice

    return {"type": "auto"}
