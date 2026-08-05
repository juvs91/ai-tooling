# llm/session/state_assertion_cache.py
"""State-assertion event audit trail (ADR-0036). Mirrors llm/session/generality_claims.py."""
from __future__ import annotations

import time

from llm.session.store import _CompressionCache, _session_cache, _state_lock, _SESSION_TTL, _save_session_cache_to_disk


async def append_session_assertion_event(
    session_id: str,
    rule_id: str,
    subject: str,
    verdict: str,
    correction_note: str,
) -> None:
    """Append a state-assertion finding to the session cache (ADR-0036).

    Persists every detected finding to keep a full audit trail — mirrors
    append_session_generality_claim/append_session_completion_claim.

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
            entry.state_assertion_events.append({
                "turn": len(entry.state_assertion_events),
                "rule_id": rule_id,
                "subject": subject,
                "verdict": verdict,
                "correction_note": correction_note,
                "timestamp": time.time(),
            })
            _save_session_cache_to_disk()
    except Exception as exc:
        print(f"[state-assertion] append_session_assertion_event failed: {exc}")


async def get_session_assertion_events(session_id: str) -> list[dict]:
    """Return the state_assertion_events audit trail for a session.

    Both shells (state_assertion_request.py/state_assertion_response.py) fetch
    this once per turn and hand the raw list to rules via
    session_snapshot["assertion_events"] — a rule that needs escalation
    counting (e.g. "has this rule_id+subject fired >=2 times?") filters that
    list directly in evaluate() (synchronous, no new cache call needed). No
    rule does this yet, so no dedicated counting helper is added here until
    one actually needs it.
    """
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is not None and time.time() - entry.timestamp < _SESSION_TTL:
            return list(entry.state_assertion_events)
    return []
