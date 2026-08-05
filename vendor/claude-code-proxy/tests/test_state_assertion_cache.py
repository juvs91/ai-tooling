# tests/test_state_assertion_cache.py
"""Tests for llm/session/state_assertion_cache.py (ADR-0036).

Mirrors the style used to exercise generality_claims/completion_claims: calls
the compressor facade re-export directly against the real in-memory session
cache (llm.session.store._session_cache), same pattern as
tests/test_intent_enforcement.py::TestAdaptiveGeneralityEnforcement.
"""
import pytest

from llm.compressor import (
    append_session_assertion_event,
    get_session_assertion_events,
)
from llm.session.store import _session_cache, _save_session_cache_to_disk


@pytest.fixture(autouse=True)
def _clean_session(request):
    """Each test uses a unique session_id (derived from the test name) and
    cleans it up after, so tests never see another test's entries.

    Must also re-save to disk (PROXY_SESSION_CACHE_FILE, default
    /tmp/proxy_session_cache.json) — append_session_assertion_event persists
    on every call, so a pop() that only clears the in-memory dict leaves the
    old entries on disk. The NEXT pytest process reloads from that file at
    import time (llm/session/store.py's _load_session_cache_from_disk()),
    silently resurrecting stale entries under the same deterministic
    session_id and making a test that looks isolated actually accumulate
    state across separate test runs.
    """
    sid = f"state-assertion-test::{request.node.name}"
    yield sid
    _session_cache.pop(sid, None)
    _save_session_cache_to_disk()


class TestAppendAndGet:
    @pytest.mark.asyncio
    async def test_append_then_get_roundtrip(self, _clean_session):
        sid = _clean_session
        await append_session_assertion_event(
            sid, "deferred_denial", "mcp__playwright__playwright_get",
            "contradicted", "note text",
        )
        events = await get_session_assertion_events(sid)
        assert len(events) == 1
        assert events[0]["rule_id"] == "deferred_denial"
        assert events[0]["subject"] == "mcp__playwright__playwright_get"
        assert events[0]["verdict"] == "contradicted"
        assert events[0]["correction_note"] == "note text"
        assert "timestamp" in events[0]
        assert events[0]["turn"] == 0

    @pytest.mark.asyncio
    async def test_multiple_events_increment_turn(self, _clean_session):
        sid = _clean_session
        await append_session_assertion_event(sid, "r1", "s1", "contradicted", "n1")
        await append_session_assertion_event(sid, "r2", "s2", "contradicted", "n2")
        events = await get_session_assertion_events(sid)
        assert [e["turn"] for e in events] == [0, 1]

    @pytest.mark.asyncio
    async def test_no_session_id_is_noop(self):
        """Empty session_id must never raise (fire-and-forget contract)."""
        await append_session_assertion_event("", "r", "s", "contradicted", "n")
        events = await get_session_assertion_events("")
        assert events == []

    @pytest.mark.asyncio
    async def test_unknown_session_returns_empty(self):
        events = await get_session_assertion_events("never-seen-session-id")
        assert events == []

    @pytest.mark.asyncio
    async def test_returned_events_carry_enough_data_for_a_rule_to_filter_itself(self, _clean_session):
        """No dedicated counting helper exists — a rule that needs to count
        prior rule_id+subject occurrences filters this raw list itself
        (it already receives it via session_snapshot["assertion_events"])."""
        sid = _clean_session
        await append_session_assertion_event(sid, "deferred_denial", "X", "contradicted", "n")
        await append_session_assertion_event(sid, "deferred_denial", "X", "contradicted", "n")
        await append_session_assertion_event(sid, "deferred_denial", "Y", "contradicted", "n")

        events = await get_session_assertion_events(sid)
        matching = [e for e in events if e["rule_id"] == "deferred_denial" and e["subject"] == "X"]
        assert len(matching) == 2
