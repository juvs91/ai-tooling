# llm/session/lifecycle.py
"""Session lifecycle: create/reuse compression cache entries, expire old ones.

Split out of llm/compressor.py — ADR-0032. Pure code motion; see the ADR-0032
plan/summary for a pre-existing bug noted (not fixed) during the move.
"""
from __future__ import annotations

import time
from typing import Optional

from utils.metrics import metrics
from llm.session.store import _CompressionCache, _session_cache, _state_lock, _SESSION_TTL, _save_session_cache_to_disk


async def get_or_create_session(session_id: str, messages: list[dict]) -> Optional[str]:
    """
    Retrieve cached summary for a session, or create a new session if it doesn't exist.

    Args:
        session_id: UUID-based session identifier
        messages: Current conversation messages

    Returns:
        Cached summary string if session exists and is valid, None otherwise
    """
    now = time.time()
    async with _state_lock:
        session = _session_cache.get(session_id)
        if session is not None:
            # Check if session is still valid (within TTL)
            if now - session.timestamp < _SESSION_TTL:
                age = int(now - session.timestamp)
                print(f"[session] Cache hit: session_id={session_id[:8]}... age={age}s summary_len={len(session.summary)}")
                metrics.compression_cache_hits += 1
                return session.summary
            else:
                print(f"[session] Session expired: session_id={session_id[:8]}... age={age}s")
                metrics.compression_cache_misses += 1
                _session_cache.pop(session_id, None)
                return None

        # Create new session
        print(f"[session] New session created: session_id={session_id[:8]}...")
        metrics.compression_cache_misses += 1
        # Note: Session will be updated with compression summary after compression completes
        return None

async def update_session(session_id: str, summary: str, old_count: int) -> None:
    """
    Update an existing session with new compression summary.

    Args:
        session_id: UUID-based session identifier
        summary: Compressed conversation summary
        old_count: Number of old messages that were compressed
    """
    now = time.time()
    async with _state_lock:
        existing = _session_cache.get(session_id)
        _session_cache[session_id] = _CompressionCache(
            session_id=session_id,
            summary=summary,
            old_msg_count=old_count,
            timestamp=now,
            deferred_tool_names=existing.deferred_tool_names if existing else [],
        )
        print(f"[session] Session updated: session_id={session_id[:8]}... old_count={old_count} summary_len={len(summary)}")
        _save_session_cache_to_disk()

async def cleanup_expired_sessions() -> None:
    """
    Remove expired sessions from cache to prevent memory leaks.
    Should be called periodically (e.g., every hour).
    """
    now = time.time()
    async with _state_lock:
        expired_sessions = [
            session_id for session_id, session in _session_cache.items()
            if now - session.timestamp >= _SESSION_TTL
        ]

        if expired_sessions:
            for session_id in expired_sessions:
                session = _session_cache.pop(session_id, None)
                age = int(now - session.timestamp)
                print(f"[session] Cleaned up expired session: session_id={str(session_id)[:8]}... age={age}s")

            if expired_sessions:
                print(f"[session] Cleanup completed: removed {len(expired_sessions)} expired sessions")
                metrics.record("sessions_cleaned", len(expired_sessions))
                _save_session_cache_to_disk()
        else:
            pass

    evicted = metrics.evict_old_sessions()
    if evicted:
        print(f"[session] Evicted {evicted} stale telemetry sessions from metrics index")
