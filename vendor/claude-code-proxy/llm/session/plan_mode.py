# llm/session/plan_mode.py
"""Plan-mode session state (ADR-0008/ADR-0010). Split out of llm/compressor.py — ADR-0032."""
from __future__ import annotations

import time

from llm.session.store import (
    _CompressionCache, _session_cache, _state_lock, _SESSION_TTL,
    _save_session_cache_to_disk,
)


async def get_session_plan_mode(session_id: str) -> bool:
    """Return cached plan_mode_active for a session, or False if not found/expired."""
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is not None and time.time() - entry.timestamp < _SESSION_TTL:
            return entry.plan_mode_active
    return False


async def set_session_plan_mode(
    session_id: str,
    active: bool,
    source: str | None = None,
    signal: str = "?",
) -> None:
    """Persist plan_mode_active into the session cache for this session.

    Args:
        session_id: The session identifier.
        active: True to activate plan mode, False to deactivate.
        source: Origin of activation — "cc" (CC /plan UI) or "proxy" (enforcement).
                Only used when active=True to set plan_mode_source.
                When active=False, plan_mode_source is reset to None.
        signal: Label identifying which signal triggered this change (for audit trail).
    """
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is not None:
            prev = entry.plan_mode_active
            entry.plan_mode_active = active
            if active and source:
                entry.plan_mode_source = source
            elif not active:
                entry.plan_mode_source = None
            if prev != active:
                action = "enter" if active else "exit"
                entry.plan_mode_events.append({
                    "turn": len(entry.plan_mode_events),  # event index (not message count)
                    "action": action,
                    "signal": signal,
                })
        else:
            _session_cache[session_id] = _CompressionCache(
                session_id=session_id,
                timestamp=time.time(),
                plan_mode_active=active,
                plan_mode_source=source if active else None,
                plan_mode_events=[{"turn": 0, "action": "enter" if active else "exit", "signal": signal}],
            )
        _save_session_cache_to_disk()


async def get_session_plan_mode_source(session_id: str) -> str | None:
    """Return the plan_mode_source ("cc", "proxy", or None) for a session."""
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is not None and time.time() - entry.timestamp < _SESSION_TTL:
            return entry.plan_mode_source
    return None


async def set_session_plan_mode_source(session_id: str, source: str | None) -> None:
    """Update only plan_mode_source without changing plan_mode_active."""
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is not None:
            entry.plan_mode_source = source
        else:
            _session_cache[session_id] = _CompressionCache(
                session_id=session_id,
                timestamp=time.time(),
                plan_mode_source=source,
            )


async def get_session_plan_mode_events(session_id: str) -> list[dict]:
    """Return the plan_mode_events audit trail for a session."""
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is not None and time.time() - entry.timestamp < _SESSION_TTL:
            return list(entry.plan_mode_events)
    return []
