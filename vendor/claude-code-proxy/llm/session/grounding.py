# llm/session/grounding.py
"""
Grounding-graph session state: multi-hop evidence tracking + persistence
(Priority 4). Split out of llm/compressor.py — ADR-0032.
"""
from __future__ import annotations

import hashlib
import os
import time

from llm.session.store import _CompressionCache, _session_cache, _state_lock, _SESSION_TTL, _save_session_cache_to_disk


async def get_session_grounding_graph(session_id: str) -> dict:
    """Return the full persisted grounding graph for a session.

    Used by GroundingValidatorTransformer to restore historical file evidence
    after compression removes old tool_result messages from context.
    """
    if not session_id:
        return {}
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is not None and time.time() - entry.timestamp < _SESSION_TTL:
            return dict(entry.grounding_graph)
    return {}


async def extend_session_grounding_graph(
    session_id: str,
    new_entities: dict,
    new_snippets: dict,
) -> None:
    """Merge new entities + snippets into the session's persistent grounding graph.

    New entries take precedence. Existing entries get updated citations and last_verified.
    Persists to disk immediately so evidence survives proxy reloads and compressions.
    """
    if not session_id:
        return
    try:
        async with _state_lock:
            entry = _session_cache.get(session_id)
            if entry is None:
                entry = _CompressionCache(session_id=session_id, timestamp=time.time())
                _session_cache[session_id] = entry
            now = time.time()
            for entity, data in new_entities.items():
                if entity not in entry.grounding_graph:
                    entry.grounding_graph[entity] = {
                        **data,
                        "first_seen": now,
                        "last_verified": now,
                    }
                else:
                    existing = entry.grounding_graph[entity]
                    existing["last_verified"] = now
                    # Merge citations (deduplicate)
                    merged_cits = list(set(existing.get("citations", []) + data.get("citations", [])))
                    existing["citations"] = merged_cits
                    # Update snippet only if new one is available
                    if data.get("code_snippet"):
                        existing["code_snippet"] = data["code_snippet"]
            # Also persist code snippets for file evidence (mapped by file_path)
            for file_path, snippet in new_snippets.items():
                # Store as a special "$file:" key for raw file lookup
                key = f"$file:{file_path}"
                if key not in entry.grounding_graph:
                    entry.grounding_graph[key] = {
                        "file": file_path,
                        "related": [],
                        "citations": [],
                        "code_snippet": snippet,
                        "first_seen": now,
                        "last_verified": now,
                    }
            _save_session_cache_to_disk()
    except Exception as exc:
        print(f"[grounding] extend_session_grounding_graph failed: {exc}")


async def get_session_read_files(session_id: str) -> set[str]:
    """Return the set of file paths that were read in this session (from grounding graph).

    Used by GroundingValidatorTransformer to validate citations against historically
    read files even after compression removed the original tool_result messages.
    """
    if not session_id:
        return set()
    async with _state_lock:
        entry = _session_cache.get(session_id)
        if entry is None or time.time() - entry.timestamp >= _SESSION_TTL:
            return set()
        return {
            v["file"]
            for k, v in entry.grounding_graph.items()
            if k.startswith("$file:") and v.get("file")
        }


async def _track_grounding_hop(
    session_id: str,
    entity_a: str,
    entity_b: str,
    evidence: list[str],
    code_snippet: str = "",
) -> None:
    """
    Track a multi-hop grounding relationship across conversation turns.

    Example: entity_a = "AuthService" → entity_b = "validateToken()"

    CAREFUL IMPLEMENTATION NOTES:
    - Only track if evidence is verified (citations exist in tool results)
    - Limit graph size to prevent memory bloat (max 100 entities)
    - Use claim hashes (not full text) to save memory
    - Prune old entries when cache is compressed
    - Never let grounding errors break the proxy (catch all exceptions)
    - Creates session if it doesn't exist (for testing and edge cases)

    Args:
        session_id: UUID-based session identifier
        entity_a: Source entity name (e.g., class name, function name)
        entity_b: Target entity name (e.g., called function, related class)
        evidence: List of citation strings (e.g., ["(auth.py:42)", "(validator.py:123)"])
        code_snippet: Actual code snippet from file (first 500 chars)
    """
    try:
        # Guard: Don't track if no verified evidence
        if not evidence:
            return

        async with _state_lock:
            # Create session if it doesn't exist
            if session_id not in _session_cache:
                _session_cache[session_id] = _CompressionCache(
                    session_id=session_id,
                    summary="",
                    old_msg_count=0,
                    timestamp=time.time()
                )

            session = _session_cache.get(session_id)
            if session is None:
                return

            # Guard: Limit graph size
            max_entities = int(os.environ.get("GROUNDING_GRAPH_MAX_ENTITIES", "100"))
            if len(session.grounding_graph) >= max_entities:
                # Prune oldest entries (simple LRU by last_seen)
                oldest_entity = min(
                    session.grounding_graph.keys(),
                    key=lambda k: session.grounding_graph[k].get("last_seen", 0)
                )
                del session.grounding_graph[oldest_entity]
                print(f"[grounding] Pruned entity: {oldest_entity} (graph size {max_entities} reached)")

            # Track entity A
            if entity_a not in session.grounding_graph:
                session.grounding_graph[entity_a] = {
                    "file": "",
                    "related": [],
                    "citations": [],
                    "code_snippet": "",
                    "last_seen": time.time()
                }

            # Track relationship A → B
            if entity_b not in session.grounding_graph[entity_a]["related"]:
                session.grounding_graph[entity_a]["related"].append(entity_b)

            # Track evidence and code snippet
            session.grounding_graph[entity_a]["citations"].extend(evidence)
            if code_snippet and not session.grounding_graph[entity_a]["code_snippet"]:
                session.grounding_graph[entity_a]["code_snippet"] = code_snippet

            # Update last seen timestamp
            session.grounding_graph[entity_a]["last_seen"] = time.time()

            # Add to verified claims (hash of claim for memory efficiency)
            for citation in evidence:
                claim_hash = hashlib.sha256(citation.encode()).hexdigest()[:16]
                session.verified_claims.add(claim_hash)

            print(f"[grounding] Tracked: {entity_a} → {entity_b} (evidence: {evidence[:2]})")
    except Exception as e:
        print(f"[grounding] Error tracking grounding hop: {e}")
        # Never let grounding errors break the proxy


async def _prune_grounding_graph(session_id: str) -> None:
    """
    Prune old entries from the grounding graph when compression happens.

    Removes entities with no recent citations (older than 10 minutes).
    This prevents memory bloat while preserving active evidence.

    Args:
        session_id: UUID-based session identifier
    """
    try:
        prune_age = int(os.environ.get("GROUNDING_GRAPH_PRUNE_AGE", "600"))  # 10 minutes
        now = time.time()

        async with _state_lock:
            session = _session_cache.get(session_id)
            if session is None:
                return

            # Initialize grounding_graph if not exists
            if not hasattr(session, "grounding_graph") or session.grounding_graph is None:
                session.grounding_graph = {}

            # Prune old entities
            entities_to_prune = []
            for entity, data in list(session.grounding_graph.items()):
                if entity == "grounding_graph":
                    continue
                if now - data.get("last_seen", 0) > prune_age:
                    entities_to_prune.append(entity)

            for entity in entities_to_prune:
                del session.grounding_graph[entity]
                print(f"[grounding] Pruned old entity: {entity} (age > {prune_age}s)")

            if entities_to_prune:
                print(f"[grounding] Pruned {len(entities_to_prune)} old entities from grounding graph")
    except Exception as e:
        print(f"[grounding] Error pruning grounding graph: {e}")
        # Never let grounding errors break the proxy


async def get_grounding_state(session_id: str) -> dict:
    """
    Retrieve the grounding state for a session.

    Returns a copy of the grounding graph to avoid mutations.

    Args:
        session_id: UUID-based session identifier

    Returns:
        Dictionary with grounding state:
        {
            "grounding_graph": {entity: {file, related, citations, code_snippet}},
            "verified_claims": set of claim hashes,
            "citation_history": list of (turn_id, citation) tuples
        }
    """
    try:
        async with _state_lock:
            session = _session_cache.get(session_id)
            if session is None:
                return {"grounding_graph": {}, "verified_claims": set(), "citation_history": []}

            return {
                "grounding_graph": session.grounding_graph.copy(),
                "verified_claims": session.verified_claims.copy(),
                "citation_history": list(session.citation_history),
            }
    except Exception as e:
        print(f"[grounding] Error getting grounding state: {e}")
        return {"grounding_graph": {}, "verified_claims": set(), "citation_history": []}
