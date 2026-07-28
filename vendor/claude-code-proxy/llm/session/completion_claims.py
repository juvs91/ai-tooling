# llm/session/completion_claims.py
"""Completion-claim audit trail (ADR-0031). Split out of llm/compressor.py — ADR-0032."""
from __future__ import annotations

import time

from llm.session.store import _CompressionCache, _session_cache, _state_lock, _SESSION_TTL, _save_session_cache_to_disk


async def append_session_completion_claim(
    session_id: str,
    claim_text: str,
    file_path: str,
    verified: bool,
    signal: str,
) -> None:
    """Append a completion-claim audit entry to the session cache (ADR-0031).

    Fire-and-forget: never raises — a failure here must not affect the response.
    """
    if not session_id:
        return
    try:
        async with _state_lock:
            entry = _session_cache.get(session_id)
            if entry is None:
                entry = _CompressionCache(session_id=session_id, timestamp=time.time())
                _session_cache[session_id] = entry
            entry.completion_claims.append({
                "turn": len(entry.completion_claims),
                "claim_text": claim_text,
                "file_path": file_path,
                "verified": verified,
                "signal": signal,
                "timestamp": time.time(),
            })
            _save_session_cache_to_disk()
    except Exception as exc:
        print(f"[grounding] append_session_completion_claim failed: {exc}")


async def get_session_completion_claims(session_id: str) -> list[dict]:
    """Return the completion_claims audit trail for a session."""
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is not None and time.time() - entry.timestamp < _SESSION_TTL:
            return list(entry.completion_claims)
    return []
