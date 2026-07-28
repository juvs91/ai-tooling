# llm/session/quality_history.py
"""Quality-score feedback loop session state. Split out of llm/compressor.py — ADR-0032."""
from __future__ import annotations

import time

from llm.session.store import _CompressionCache, _session_cache, _state_lock, _SESSION_TTL, _save_session_cache_to_disk


async def get_session_quality_history(session_id: str) -> tuple[list[float], int]:
    """Return (quality_scores, session_stub_count) for a session."""
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is not None and time.time() - entry.timestamp < _SESSION_TTL:
            return list(entry.quality_scores), entry.session_stub_count
    return [], 0


async def append_session_quality(session_id: str, quality_score: float, stub_delta: int = 0) -> None:
    """Append quality_score and accumulate stubs into the session cache.

    Keeps only the last 10 scores to bound memory and keep averages current.
    Persists to disk immediately so the history survives proxy reloads.
    """
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is None:
            entry = _CompressionCache(session_id=session_id, timestamp=time.time())
            _session_cache[session_id] = entry
        entry.quality_scores.append(quality_score)
        if len(entry.quality_scores) > 10:
            entry.quality_scores = entry.quality_scores[-10:]
        entry.session_stub_count += stub_delta
        _save_session_cache_to_disk()
