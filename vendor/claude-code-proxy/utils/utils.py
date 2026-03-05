# app/utils/utils.py
from __future__ import annotations

import hashlib
import json
import uuid
from threading import Lock
from typing import Any, Optional, Set, Tuple


# ── Unified Accessors ────────────────────────────────────────────────
# Pydantic models and dicts coexist throughout the proxy.
# These helpers eliminate the "isinstance dance" from call sites.

def bget(obj: Any, key: str, default: Any = None) -> Any:
    """Access a field uniformly from a Pydantic model or a dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def get_tool_name(tool: Any) -> str:
    """Extract the tool name from a tool definition (dict or Pydantic)."""
    return (bget(tool, "name") or "").strip()


# ── Anthropic-Format Helpers ─────────────────────────────────────────

TOOL_ID_PREFIX = "toolu_"


def make_tool_id() -> str:
    """Generate a unique tool_use ID in Anthropic's expected format."""
    return f"{TOOL_ID_PREFIX}{uuid.uuid4().hex[:24]}"


def to_dict(obj: Any) -> Any:
    """Convert a Pydantic model to a plain dict; pass-through for dicts."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=True)
    if hasattr(obj, "dict"):
        return obj.dict(exclude_none=True)
    return obj


# ── Stop Reason Mapping ─────────────────────────────────────────────
# OpenAI uses "finish_reason"; Anthropic uses "stop_reason" with
# different vocabulary.  This single mapping is the source of truth.

_STOP_REASON_MAP = {
    "stop": "end_turn",
    "end_turn": "end_turn",
    "length": "max_tokens",
    "max_tokens": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}


def map_stop_reason(finish_reason: str | None, has_tool_use: bool = False) -> str:
    """Map an OpenAI/LiteLLM finish_reason to an Anthropic stop_reason."""
    if has_tool_use:
        return "tool_use"
    return _STOP_REASON_MAP.get(finish_reason or "", "end_turn")

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

_CLAUDE_ASSUMED_CONTEXT = 200_000

def scale_tokens(raw_count: int, model_context_window: int) -> int:
    """
    Scale a raw token count so Claude Code's internal heuristics
    (which assume a 200K context window) trigger at the right time.
    If model_context_window is 0 or >= 200K, returns the raw count unchanged.
    """
    if model_context_window <= 0 or model_context_window >= _CLAUDE_ASSUMED_CONTEXT:
        return raw_count
    return int(raw_count * (_CLAUDE_ASSUMED_CONTEXT / model_context_window))

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
        name = get_tool_name(t)
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
        name = get_tool_name(t)
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


# ── Token Count Cache (per-message incremental) ─────────────────────
# Previous approach hashed the entire messages array → 2.5% hit rate
# because messages change every turn. This approach caches per-message
# token counts — only new messages need counting (~95% hit rate).

_per_msg_cache: dict[str, int] = {}
_per_msg_cache_lock = Lock()
_PER_MSG_MAX = 1024


def _hash_single_msg(msg: dict, model: str) -> str:
    """Hash a single message for per-message token caching."""
    raw = json.dumps(
        {"role": msg.get("role", ""), "content": msg.get("content", ""), "model": model},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def cached_token_count(messages: list, model: str, system: str | None = None) -> int | None:
    """Incremental: sum per-message cached counts. Returns None if ANY message is uncached."""
    total = 0
    with _per_msg_cache_lock:
        for msg in messages:
            key = _hash_single_msg(msg, model)
            cached = _per_msg_cache.get(key)
            if cached is None:
                return None
            total += cached
    return total


def store_token_count(messages: list, model: str, count: int, system: str | None = None):
    """Store per-message counts using proportional split from total count."""
    if not messages:
        return
    total_chars = sum(len(str(m.get("content", ""))) for m in messages) or 1
    with _per_msg_cache_lock:
        for msg in messages:
            key = _hash_single_msg(msg, model)
            if key not in _per_msg_cache:
                chars = len(str(msg.get("content", "")))
                _per_msg_cache[key] = max(1, int(count * chars / total_chars))
        # Evict oldest if over limit
        while len(_per_msg_cache) > _PER_MSG_MAX:
            oldest = next(iter(_per_msg_cache))
            del _per_msg_cache[oldest]
