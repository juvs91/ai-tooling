# llm/session/store.py
"""
Shared session-cache foundation (ADR-0032).

`_CompressionCache` is the single per-session state container every
`llm/session/*` module reads and writes through — plan mode, deferred tools,
quality history, grounding graph, and completion claims (ADR-0031) all live on
this one dataclass. Historically this all lived inline in `llm/compressor.py`;
it grew far beyond "compression cache" into a general session KV-store, hence
the split.

`_session_cache` (dict) and `_state_lock` (asyncio.Lock) are imported by every
other `llm/session/*` module — never redefined there, so mutation stays visible
across modules (dicts/locks are mutable objects; only rebinding the name itself
would break sharing, and nothing here does that).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class _CompressionCache:
    summary: str = ""              # The compressed summary text
    old_msg_count: int = 0         # How many old messages were compressed
    timestamp: float = 0.0         # time.time() (Unix epoch) when cached
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))  # UUID-based session ID
    prefix_hash: str = field(default_factory=lambda: hashlib.sha256(b"").hexdigest()[:16])  # For backward compatibility

    # Grounding state (multi-hop evidence tracking)
    grounding_graph: dict[str, dict] = field(default_factory=dict)
    # Entity → {file, related, citations, code_snippet, last_seen}
    verified_claims: set[str] = field(default_factory=set)
    # Set of claim hashes that have been verified across conversation
    citation_history: list[tuple[str, str]] = field(default_factory=list)
    # List of (turn_id, citation) tuples for multi-hop tracking

    # Deferred tools cache — persists CC's <available-deferred-tools> list
    # across turns so injection never depends on CC re-sending the block.
    deferred_tool_names: list[str] = field(default_factory=list)
    # Plan mode state — persists across history truncation and proxy restarts.
    # Set True on first EnterPlanMode/PLAN-intent turn; cleared on ExitPlanMode.
    plan_mode_active: bool = False
    # Origin of the current plan session: "cc" (CC /plan UI) or "proxy" (enforcement-initiated).
    # Used by Signal 4 to prevent false-positive exits on proxy-initiated plans. See ADR-0010.
    plan_mode_source: str | None = None
    # Audit trail for plan mode activations/deactivations this session.
    plan_mode_events: list[dict] = field(default_factory=list)

    # Completion-claim audit trail (ADR-0031) — "ya arreglé X" style claims checked
    # against actual Edit/Write/MultiEdit activity in the conversation.
    completion_claims: list[dict] = field(default_factory=list)
    # {"turn": int, "claim_text": str, "file_path": str, "verified": bool,
    #  "signal": "strong"|"weak", "timestamp": float}

    # Quality feedback loop (Item 4) — proxy-internal session history.
    # Used by intent_enforcement.py to escalate enforcement when quality is consistently low.
    # Populated by quality_refinement.py after every response that has a quality score.
    quality_scores: list[float] = field(default_factory=list)  # last N quality scores (0.0–1.0)
    session_stub_count: int = 0                                 # stubs detected so far in this session

    # Priority 2: structured state (entities, decisions, phase checkpoints) extracted before
    # each compression and injected back into the system prompt after reassembly.
    session_state: Optional[dict] = None  # serialized SessionState.to_dict()


_session_cache: Dict[str, _CompressionCache] = {}  # Multi-session support: session_id -> cache entry
_SESSION_TTL = 604800.0          # 7 days — matches typical dev session rhythm (survive weekend gaps)
_CACHE_MSG_TOLERANCE = 100   # Reuse if ≤100 new old messages since last compression
_CACHE_PREFIX_SIZE = 20      # Hash first 20 messages for session identity

# Disk persistence — survives uvicorn --reload and proxy restarts
_SESSION_CACHE_FILE = os.environ.get("PROXY_SESSION_CACHE_FILE", "/tmp/proxy_session_cache.json")
_SESSION_CACHE_MAX_MB = int(os.environ.get("PROXY_SESSION_CACHE_MAX_MB", "1024"))

_MAX_SESSION_STATE_ENTITIES = 150  # cap entities per session to bound session_state size
_MAX_CITATION_HISTORY = 200        # cap citation history per session

# Lock for all module-level mutable state shared across llm/session/* and the
# compression circuit-breaker state that stays in llm/compressor.py.
_state_lock = asyncio.Lock()


def _compute_prefix_hash(messages: list[dict], n: int = _CACHE_PREFIX_SIZE) -> str:
    """Hash the first N messages to identify the conversation session."""
    prefix = messages[:n]
    raw = json.dumps(prefix, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _save_session_cache_to_disk() -> None:
    """Persist _session_cache to JSON. Must be called within _state_lock.

    Evicts expired sessions before saving so disk and in-memory cache stay clean.
    Trims per-session fields that grow unbounded (session_state.entities, citation_history).
    """
    try:
        now = time.time()
        expired = [sid for sid, c in _session_cache.items() if now - c.timestamp >= _SESSION_TTL]
        for sid in expired:
            del _session_cache[sid]
        if expired:
            print(f"[session] Cache cleanup: removed {len(expired)} expired session(s)")

        data = {}
        for sid, c in _session_cache.items():
            # Trim session_state.entities to prevent unbounded growth
            ss = c.session_state
            if ss and len(ss.get("entities", {})) > _MAX_SESSION_STATE_ENTITIES:
                trimmed_entities = dict(list(ss["entities"].items())[-_MAX_SESSION_STATE_ENTITIES:])
                ss = {**ss, "entities": trimmed_entities}

            data[str(sid) if sid is not None else "__default__"] = {
                "summary": c.summary,
                "old_msg_count": c.old_msg_count,
                "timestamp": c.timestamp,
                "session_id": c.session_id,
                "grounding_graph": c.grounding_graph,
                "verified_claims": list(c.verified_claims),
                "citation_history": c.citation_history[-_MAX_CITATION_HISTORY:],
                "deferred_tool_names": c.deferred_tool_names,
                "plan_mode_active": c.plan_mode_active,
                "plan_mode_source": c.plan_mode_source,
                "plan_mode_events": c.plan_mode_events[-50:],  # cap at 50 events
                "completion_claims": c.completion_claims[-50:],  # cap at 50 events
                "quality_scores": c.quality_scores,
                "session_stub_count": c.session_stub_count,
                "session_state": ss,
            }

        with open(_SESSION_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[session] Failed to persist cache to disk: {e}")


def _load_session_cache_from_disk() -> None:
    """Restore _session_cache from JSON on startup. Skips expired entries and clears if oversized."""
    if not os.path.exists(_SESSION_CACHE_FILE):
        return
    try:
        size_mb = os.path.getsize(_SESSION_CACHE_FILE) / (1024 * 1024)
        if size_mb > _SESSION_CACHE_MAX_MB:
            print(f"[session] Cache file {size_mb:.0f}MB exceeds {_SESSION_CACHE_MAX_MB}MB limit — clearing")
            os.remove(_SESSION_CACHE_FILE)
            return
    except OSError:
        pass
    try:
        with open(_SESSION_CACHE_FILE) as f:
            data = json.load(f)
        now = time.time()
        loaded = 0
        for raw_sid, entry in data.items():
            ts = entry.get("timestamp", 0.0)
            if now - ts >= _SESSION_TTL:
                continue  # expired — skip
            sid = None if raw_sid == "__default__" else raw_sid
            _session_cache[sid] = _CompressionCache(
                session_id=entry.get("session_id", str(raw_sid)),
                summary=entry.get("summary", ""),
                old_msg_count=entry.get("old_msg_count", 0),
                timestamp=ts,
                grounding_graph=entry.get("grounding_graph", {}),
                verified_claims=set(entry.get("verified_claims", [])),
                citation_history=entry.get("citation_history", []),
                deferred_tool_names=entry.get("deferred_tool_names", []),
                plan_mode_active=entry.get("plan_mode_active", False),
                plan_mode_source=entry.get("plan_mode_source"),
                plan_mode_events=entry.get("plan_mode_events", []),
                completion_claims=entry.get("completion_claims", []),
                quality_scores=entry.get("quality_scores", []),
                session_stub_count=entry.get("session_stub_count", 0),
                session_state=entry.get("session_state"),
            )
            loaded += 1
        if loaded:
            print(f"[session] Restored {loaded} session(s) from {_SESSION_CACHE_FILE}")
            grounding_loaded = sum(1 for c in _session_cache.values() if c.grounding_graph)
            if grounding_loaded:
                print(f"[session] Restored grounding state for {grounding_loaded} session(s)")
    except Exception as e:
        print(f"[session] Failed to load cache from disk: {e}")


_load_session_cache_from_disk()  # restore sessions from previous proxy run
