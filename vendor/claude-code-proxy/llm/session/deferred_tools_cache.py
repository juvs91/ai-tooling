# llm/session/deferred_tools_cache.py
"""Deferred-tools session cache — persists CC's <available-deferred-tools> list
across turns so injection never depends on CC re-sending the block.

Split out of llm/compressor.py — ADR-0032. Not to be confused with
llm/transformers/deferred_tools.py (the transformer that injects tool
definitions using this cache).
"""
from __future__ import annotations

import time

from llm.session.store import _CompressionCache, _session_cache, _state_lock, _SESSION_TTL, _save_session_cache_to_disk


async def get_session_deferred_tools(session_id: str) -> list[str]:
    """Return cached deferred tool names for a session, or [] if not found/expired."""
    async with _state_lock:
        session = _session_cache.get(session_id)
        if session is not None and time.time() - session.timestamp < _SESSION_TTL:
            return list(session.deferred_tool_names)
    return []


async def save_session_deferred_tools(session_id: str, tool_names: list[str]) -> None:
    """Persist deferred tool names into the session cache for this session."""
    async with _state_lock:
        session = _session_cache.get(session_id)
        if session is not None:
            session.deferred_tool_names = list(tool_names)
        else:
            _session_cache[session_id] = _CompressionCache(
                session_id=session_id,
                timestamp=time.time(),
                deferred_tool_names=list(tool_names),
            )
        _save_session_cache_to_disk()
