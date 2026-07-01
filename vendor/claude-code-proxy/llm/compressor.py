# llm/compressor.py
"""
Context compression for models with limited context windows.

When a conversation exceeds the model's context window, this module:
  1. Keeps system prompt + recent messages intact
  2. Summarizes older messages using a cheap LLM call
  3. Reassembles: [system] + [summary] + [recent messages]
  4. Falls back to simple trimming if the compressor fails

Resilience layers:
  - Retry with exponential backoff (3 attempts per endpoint)
  - Circuit breaker: skip compressor for 60s after 5 consecutive failures
  - Fallback compressor: try a secondary endpoint if primary fails
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Dict
import litellm
from litellm import token_counter
from utils.metrics import metrics
from utils.utils import count_tokens_accurate  # toksum integration
from llm.session_state import extract_session_state, inject_state_into_system_prompt, SessionState, extract_todo_state


_MAX_SESSION_STATE_ENTITIES = 150  # cap entities per session to bound session_state size
_MAX_CITATION_HISTORY = 200        # cap citation history per session


# Circuit breaker state (module-level, persists across requests)
_consecutive_failures: int = 0
_circuit_open_until: float = 0.0
_CIRCUIT_BREAKER_THRESHOLD = 5   # failures before opening circuit
_CIRCUIT_BREAKER_COOLDOWN = 60.0  # seconds to skip compressor after circuit opens

# Compression token budget to limit DeepSeek calls per session
_COMPRESSION_TOKEN_BUDGET = 50000  # Max tokens to spend on DeepSeek per session
_compression_tokens_spent: dict[str, int] = {}  # session_id -> tokens spent

# Lock for all module-level mutable state (_compression_cache, _consecutive_failures, _circuit_open_until)
_state_lock = asyncio.Lock()


# ── Compression result cache ──────────────────────────────────────────
# CC is stateless: sends full conversation history on every request.
# Without caching, the proxy re-compresses ~1700 identical messages via
# a DeepSeek LLM call on every single request (2-5s + API cost each).
# This cache stores the last compression summary and reuses it when the
# same session sends a near-identical request.
# Works for ALL providers (Z.AI Anthropic, Z.AI OpenAI, DeepSeek).

# Session Management Enhancement (Phase 3):
# - Supports multiple concurrent sessions with explicit session IDs
# - Maintains conversation continuity across proxy restarts and profile changes
# - Uses UUID-based session IDs passed via X-Session-ID HTTP header
# - Cache TTL extended to 24 hours for longer-term persistence

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
                plan_modee_source=entry.get("plan_mode_source"),
                plan_mode_events=entry.get("plan_mode_events", []),
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
    except Exception as e:
        print(f"[session] Failed to load cache from disk: {e}")


_load_session_cache_from_disk()  # restore sessions from previous proxy run


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


_COMPRESS_PROMPT = (
    "You are a conversation summarizer. Summarize the following conversation context concisely.\n\n"
    "RULES:\n"
    "- PRESERVE: file paths, tool names, function names, error messages, key decisions, code snippets\n"
    "- PRESERVE: which tasks were completed and which are still pending\n"
    "- PRESERVE: which files were modified (Edit/Write) vs only read\n"
    "- REMOVE: verbose tool outputs, repetitive explanations, intermediate reasoning\n"
    "- Keep the summary under 2000 tokens\n"
    "- Structure: '## Completed Work' → '## Pending Work' → '## Files Modified' → '## Key Decisions'\n"
    "\nConversation to summarize:\n{conversation}\n\n"
    "Concise summary:"
)


# _count_message_tokens() replaced by count_tokens_accurate() from utils/utils.py
# This provides toksum integration with LiteLLM fallback and character approximation
# All usages replaced below


def estimate_tools_tokens(tools: list[dict] | None) -> int:
    """Estimate token overhead from OpenAI-format tool definitions."""
    if not tools:
        return 0
    total_chars = 0
    for tool in tools:
        try:
            total_chars += len(json.dumps(tool))
        except Exception:
            total_chars += 500  # conservative fallback per tool
    return total_chars // 3


def _find_safe_split_point(conversation: list[dict], keep_recent: int) -> int:
    """Find split point that preserves tool_use/tool_result pairs.

    When compressing, we split conversation into old (compressed) and recent
    (kept intact). If a role:"tool" message in recent references a tool_call_id
    from an assistant message in old, the API rejects with error 2013.

    This function scans backward from the naive split to include any assistant
    messages whose tool_calls are referenced by tool messages in recent.
    """
    if len(conversation) <= keep_recent:
        return 0

    split = len(conversation) - keep_recent

    # Collect tool_call_ids referenced in the recent portion
    referenced_ids = set()
    for msg in conversation[split:]:
        if msg.get("role") == "tool":
            tid = msg.get("tool_call_id")
            if tid:
                referenced_ids.add(tid)

    if not referenced_ids:
        return split

    # Scan backward from split to find their parent assistant messages
    for i in range(split - 1, -1, -1):
        msg = conversation[i]
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                if tc_id in referenced_ids:
                    split = i
                    referenced_ids.discard(tc_id)
        if not referenced_ids:
            break

    return split


def _serialize_messages_for_summary(messages: list[dict], max_chars: int = 50000) -> str:
    """Serialize messages to text for the compressor, truncating large outputs.

    Tool results get higher char limits (6000) to preserve file contents and
    error messages that are critical for continued tool use.
    """
    lines = []
    chars = 0
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "") or ""
        # Tool results get higher limit — they contain file contents/errors
        # that are critical for the model to continue working correctly
        limit = 6000 if role == "tool" else 3000
        if len(content) > limit:
            keep_end = min(1000, limit // 4)
            keep_start = limit - keep_end - 30  # 30 chars for truncation marker
            content = content[:keep_start] + "\n...[truncated]...\n" + content[-keep_end:]
        line = f"[{role}]: {content}"
        if chars + len(line) > max_chars:
            lines.append("[...earlier messages omitted...]")
            break
        lines.append(line)
        chars += len(line)
    return "\n\n".join(lines)


def _normalize_messages(messages: list[dict]) -> list[dict]:
    """
    Normalize messages to ensure consistent schema before processing.

    Preserves tool_calls and tool_call_id for tool reference validation.
    Pure normalization logic, no hardcoded values.
    """
    normalized = []
    for m in messages:
        role = m.get("role", "user")
        normalized_msg = {
            "role": role,
            "content": m.get("content", "")
        }
        # Preserve tool_calls for assistant messages (needed for tool reference validation)
        if role == "assistant" and "tool_calls" in m:
            normalized_msg["tool_calls"] = m["tool_calls"]
        # Preserve tool_call_id for tool messages (needed for tool reference validation)
        if role == "tool" and "tool_call_id" in m:
            normalized_msg["tool_call_id"] = m["tool_call_id"]
        normalized.append(normalized_msg)
    return normalized


def _detect_tool_inflation(messages: list[dict], tool_inflation_threshold: int) -> bool:
    """
    Detect tool message inflation in the conversation.

    Returns True if tool count > threshold, False otherwise.
    Tracks detection in metrics.
    """
    tool_count = sum(
        1 for m in messages
        if m.get("role") == "tool"
    )
    is_inflated = tool_count > tool_inflation_threshold
    if is_inflated:
        metrics.compression_tool_inflation_detected += 1
    return is_inflated


def _split_conversation(
    messages: list[dict],
    model_context_window: int,
    summary_trigger_ratio: float,
    recent_window_ratio: float,
    message_threshold: int = 20,  # Use message count threshold for early compression
    avg_tokens_per_msg: float = 300.0,  # Dynamic: computed from actual session data
) -> tuple[list[dict], list[dict]]:
    """
    Split conversation into old (to be summarized) and recent (to keep intact).

    All thresholds are calculated dynamically from config ratios - no magic numbers.
    avg_tokens_per_msg is passed from compress_messages() using actual token count /
    message count, so the recent window adapts to the real session density.
    """
    # Calculate dynamic thresholds from config ratios
    summary_trigger_tokens = int(model_context_window * summary_trigger_ratio)
    recent_window_tokens = int(model_context_window * recent_window_ratio)

    # Use actual avg tok/msg (not hardcoded 300) — analysis sessions average 700+ tok/msg
    # (tool results + file reads), which would inflate recent_window_msgs to 300 with the
    # old assumption and swallow the entire conversation into the "recent" window.
    recent_window_msgs = max(10, int(recent_window_tokens / avg_tokens_per_msg))

    # Not enough messages to split — all messages fall inside the recent window.
    # NOTE: do NOT add message_threshold here; that's the trigger threshold (checked
    # upstream in compress_messages). Adding it here blocked compression for all sessions
    # under (message_threshold + recent_window_msgs) = 510 messages.
    if len(messages) <= recent_window_msgs:
        return [], messages

    # Split into old and recent
    # Keep last recent_window_msgs messages as recent, everything else as old
    split_point = len(messages) - recent_window_msgs
    old_messages = messages[:split_point]
    recent_messages = messages[split_point:]

    print(f"[compress] Split conversation: {len(old_messages)} old messages, {len(recent_messages)} recent messages "
          f"(threshold={message_threshold}, keep_recent={recent_window_msgs}, "
          f"summary_trigger_tokens={summary_trigger_tokens}, recent_window_tokens={recent_window_tokens})")

    return old_messages, recent_messages


def _trim_by_token_budget(
    messages: list[dict],
    max_tokens: int,
    target_model: str = "",
) -> list[dict]:
    """
    Remove oldest messages until token budget fits.

    max_tokens is passed from caller (calculated from config).
    Tracks aggressive trims in metrics.
    """
    current_tokens = count_tokens_accurate(messages, model=target_model)
    if current_tokens <= max_tokens:
        return messages

    print(f"[compress] Token budget exceeded: {current_tokens} > {max_tokens}, trimming...")

    # Remove oldest messages until we fit the budget
    trimmed = messages.copy()
    while len(trimmed) > 10:  # Keep at least 10 messages minimum
        current_tokens = count_tokens_accurate(trimmed, model=target_model)
        if current_tokens <= max_tokens:
            break
        trimmed.pop(0)

    new_tokens = count_tokens_accurate(trimmed, model=target_model)
    metrics.compression_aggressive_trims += 1
    print(f"[compress] Trimmed to {len(trimmed)} messages: {current_tokens} → {new_tokens} tokens")
    return trimmed


def _enforce_message_cap(
    messages: list[dict],
    max_messages: int,
) -> list[dict]:
    """
    Enforce hard message cap.

    max_messages is passed from caller (calculated from config).
    Tracks message cap enforcement in metrics.
    """
    if len(messages) <= max_messages:
        return messages

    print(f"[compress] Message cap exceeded: {len(messages)} > {max_messages}, enforcing cap...")
    metrics.compression_message_cap_enforced += 1
    # Keep only the most recent max_messages
    capped = messages[-max_messages:]
    print(f"[compress] Capped to {len(capped)} messages")
    return capped


async def _apply_preserved_state(
    messages: list[dict],
    session_id: str,
    source_messages: list[dict],
    full_messages: list[dict] | None = None,
) -> list[dict]:
    """Extract structured state from source_messages and inject into system prompt.

    Merges with any previously persisted state so checkpoint history accumulates
    across multiple compression boundaries (not just the current one).

    full_messages: complete history (old + recent) for TodoWrite scan — recent_messages
    are not included in source_messages (old_messages only), so the last TodoWrite
    call is often invisible to extract_session_state without this parameter.
    """
    try:
        async with _state_lock:
            entry = _session_cache.get(session_id)
            existing_raw = entry.session_state if entry else None
        existing = SessionState.from_dict(existing_raw) if existing_raw else None

        state = extract_session_state(source_messages, existing)

        if full_messages:
            todo_items = extract_todo_state(full_messages)
            if todo_items:
                state.todos = todo_items

        async with _state_lock:
            entry = _session_cache.get(session_id)
            if entry:
                entry.session_state = state.to_dict()

        result = list(messages)
        for i, msg in enumerate(result):
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    result[i] = {**msg, "content": inject_state_into_system_prompt(content, state)}
                elif isinstance(content, list):
                    text_idx = next(
                        (j for j, b in enumerate(content) if isinstance(b, dict) and b.get("type") == "text"),
                        None,
                    )
                    if text_idx is not None:
                        blocks = list(content)
                        blocks[text_idx] = {
                            **blocks[text_idx],
                            "text": inject_state_into_system_prompt(blocks[text_idx].get("text", ""), state),
                        }
                        result[i] = {**msg, "content": blocks}
                break

        print(
            f"[compress] State preserved: {len(state.checkpoints)} checkpoints, "
            f"{len(state.decisions)} decisions, {len(state.entities)} entities"
        )
        return result
    except Exception as exc:
        print(f"[compress] State preservation failed (non-fatal): {exc}")
        return messages


async def compress_messages_if_needed(
    messages: list[dict],
    cfg: Any,  # CompressorConfig with all new parameters
    model_context_window: int,
    compressor_model: str,
    compressor_api_key: str,
    compressor_base_url: Optional[str] = None,
    tools_overhead_tokens: int = 0,
    target_model: str = "",
    session_id: str = "",  # Phase 3: Session ID management
) -> tuple[list[dict], bool]:
    """
    Compress conversation if it exceeds the model's context window.

    Multi-layer pipeline: normalize → detect inflation → split → summarize → merge → trim → cap
    All limits are dynamically calculated from model_context_window and config ratios - no magic numbers.

    Args:
        messages: OpenAI-format messages (already converted from Anthropic)
        cfg: CompressorConfig with all compression parameters
        model_context_window: Target model's context window in tokens
        compressor_model: LiteLLM model string for the compressor (e.g. "openai/glm-4.7-flash")
        compressor_api_key: API key for the compressor
        compressor_base_url: Optional base URL for the compressor
        tools_overhead_tokens: Extra tokens from tool definitions (not in messages)
        target_model: LiteLLM model string for the target model (used for accurate token counting)

    Returns:
        (messages, was_compressed) — compressed messages and whether compression happened
    """
    if model_context_window <= 0 or not compressor_model or not compressor_api_key:
        return messages, False

    # Step 1: Normalize messages
    messages = _normalize_messages(messages)

    # Step 2: Detect tool inflation
    if _detect_tool_inflation(messages, cfg.tool_inflation_threshold):
        print(f"[compress] Tool inflation detected: >{cfg.tool_inflation_threshold} tool messages")

    estimated_tokens = count_tokens_accurate(messages, model=target_model) + tools_overhead_tokens
    threshold = int(cfg.trigger_ratio * model_context_window)

    # Compute dynamic avg tok/msg from actual session data.
    # Analysis sessions average 700+ tok/msg vs the old hardcoded 300 assumption.
    # Used for recent_window_msgs in _split_conversation and max_messages cap below.
    msg_count = len(messages)
    msg_only_tokens = max(1, estimated_tokens - tools_overhead_tokens)
    avg_tokens_per_msg = max(100.0, msg_only_tokens / msg_count) if msg_count > 0 else 300.0

    # Calculate dynamic limits from config ratios (uses avg_tokens_per_msg, not hardcoded 300)
    max_messages = int(model_context_window * cfg.max_messages_ratio / avg_tokens_per_msg)
    max_tokens = int(model_context_window * cfg.max_tokens_ratio)
    summary_trigger_tokens = int(model_context_window * cfg.summary_trigger_ratio)
    recent_window_tokens = int(model_context_window * cfg.recent_window_ratio)

    print(f"[compress] Dynamic limits for model (context_window={model_context_window}): "
          f"max_messages={max_messages}, max_tokens={max_tokens}, "
          f"summary_trigger={summary_trigger_tokens} tokens, recent_window={recent_window_tokens} tokens "
          f"avg_tok_per_msg={avg_tokens_per_msg:.0f}")

    print(f"[compress] Check: tokens={estimated_tokens} (tools_overhead={tools_overhead_tokens}) "
          f"threshold={threshold} (window={model_context_window} × ratio={cfg.trigger_ratio}) "
          f"model={target_model} msg_count={len(messages)}")

    # HYBRID TRIGGER: Token count OR message count (whichever comes first)
    if estimated_tokens <= threshold and len(messages) < cfg.message_threshold:
        return messages, False

    if len(messages) >= cfg.message_threshold:
        print(f"[compress] TRIGGERED BY MESSAGE COUNT: {len(messages)} >= {cfg.message_threshold}")
        # Continue to compression logic below

    # Step 3: Split conversation into old and recent parts
    old_messages, recent_messages = _split_conversation(
        messages,
        model_context_window,
        cfg.summary_trigger_ratio,
        cfg.recent_window_ratio,
        cfg.message_threshold,
        avg_tokens_per_msg=avg_tokens_per_msg,
    )

    # Not enough old messages to compress
    if len(old_messages) < 3:
        print(f"[compress] Skipped: only {len(old_messages)} old msgs (need >= 3)")
        return messages, False

    print(f"[compress] Triggered: {estimated_tokens} tokens > {threshold} threshold "
          f"OR {len(messages)} >= {cfg.message_threshold} messages. "
          f"Compressing {len(old_messages)} old messages, keeping {len(recent_messages)} recent. "
          f"compressor={compressor_model}")

    # Extract system message
    system_msg = None
    if messages and messages[0].get("role") == "system":
        system_msg = messages[0]

    # ── Derive stable cache key ──
    # Explicit X-Session-ID takes priority; otherwise generate a deterministic UUID from
    # the conversation prefix so each CC window gets its own isolated cache slot.
    # FIX: Use full messages instead of old_messages for stable session ID
    effective_session_id = session_id if session_id else str(
        uuid.uuid5(uuid.NAMESPACE_OID, _compute_prefix_hash(messages, _CACHE_PREFIX_SIZE))
    )

    # ── Check compression cache before calling LLM ──
    cached_summary = await get_or_create_session(effective_session_id, old_messages)
    if cached_summary is not None:
        # Cache hit — reuse previous summary, skip the LLM call
        metrics.compression_cache_hits += 1
        now = time.time()
        cache = _session_cache.get(effective_session_id)
        age = int(now - cache.timestamp) if cache else 0
        delta = len(old_messages) - cache.old_msg_count if cache else 0
        print(f"[compress] Cache HIT: reusing summary "
              f"(session={effective_session_id}, cached {cache.old_msg_count if cache else 0} msgs, now {len(old_messages)} msgs, "
              f"delta={delta}, age={age}s)")
    else:
        metrics.compression_cache_misses += 1
        print(f"[compress] Cache MISS (no session): compressing fresh (session={effective_session_id})")

    # ── Prune grounding graph if compression happened ──
    # This runs async in the background to not delay compression
    if effective_session_id:
        asyncio.create_task(_prune_grounding_graph(effective_session_id))

    if cached_summary is not None:
        compressed = _reassemble_with_summary(system_msg, cached_summary, recent_messages)
        compressed = await _apply_preserved_state(compressed, effective_session_id, old_messages, full_messages=messages)
        new_tokens = count_tokens_accurate(compressed, model=target_model)
        print(f"[compress] Success (cached): {estimated_tokens} → {new_tokens} tokens "
              f"(saved {estimated_tokens - new_tokens})")
        return compressed, True

    # Try LLM compression (retry + circuit breaker + fallback)
    result = await _llm_compress(
        old_messages, compressor_model, compressor_api_key, compressor_base_url,
        fallback_model=cfg.fallback_model,
        fallback_api_key=cfg.fallback_api_key,
        fallback_base_url=cfg.fallback_base_url,
    )

    if result:
        summary, model_used = result
        # Store in cache for next request
        await update_session(effective_session_id, summary, len(old_messages))
        # Reassemble with summary
        merged = _reassemble_with_summary(system_msg, summary, recent_messages)
        merged = await _apply_preserved_state(merged, effective_session_id, old_messages, full_messages=messages)

        # Step 5: Enforce token budget
        merged = _trim_by_token_budget(merged, max_tokens, target_model)

        # Step 6: Enforce message cap
        merged = _enforce_message_cap(merged, max_messages)

        new_tokens = count_tokens_accurate(merged, model=target_model)
        print(f"[compress] Success ({model_used}): {estimated_tokens} → {new_tokens} tokens "
              f"(saved {estimated_tokens - new_tokens})")
        return merged, True

    # Fallback: aggressive trimming — keep only cfg.keep_recent most recent messages
    # CC resends full conversation next turn, so trimming more aggressively
    # prevents the regrowth cycle where tokens grow 12K→218K.
    aggressive_keep = min(cfg.keep_recent, len(recent_messages))
    aggressive_recent = recent_messages[-aggressive_keep:] if aggressive_keep > 0 else recent_messages
    print(f"[compress] LLM compression failed, falling back to aggressive trimming "
          f"(keeping {len(aggressive_recent)} of {len(messages)} messages)")
    trimmed = _reassemble_trimmed(system_msg, aggressive_recent)

    # Apply token budget and message cap to fallback as well
    trimmed = _trim_by_token_budget(trimmed, max_tokens, target_model)
    trimmed = _enforce_message_cap(trimmed, max_messages)

    new_tokens = count_tokens_accurate(trimmed, model=target_model)
    print(f"[compress] Trimmed: {estimated_tokens} → {new_tokens} tokens")
    return trimmed, True


async def _llm_compress_single(
    prompt: str,
    model: str,
    api_key: str,
    api_base: Optional[str],
    retries: int = 3,
    label: str = "primary",
) -> Optional[str]:
    """
    Call a single compressor endpoint with retry + exponential backoff.
    Returns summary string or None on failure.
    """

    for attempt in range(retries):
        print(f"[compress] {label} calling {model} (attempt {attempt + 1}/{retries})")
        try:
            # Check token budget for this session
            session_budget = _compression_tokens_spent.get(model, 0)
            if session_budget > _COMPRESSION_TOKEN_BUDGET:
                print(f"[compress] Token budget exceeded for model {model}, using simple trimming")
                return None

            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2048,
                "temperature": 0,
                "stream": False,
            }
            if api_key:
                kwargs["api_key"] = api_key
            if api_base:
                kwargs["api_base"] = api_base

            resp = await litellm.acompletion(**kwargs)
            summary = (resp.choices[0].message.content or "").strip()

            if len(summary) < 20:
                print(f"[compress] {label} returned too-short summary ({len(summary)} chars)")
                return None

            return summary

        except Exception as e:
            wait = 2 ** attempt  # 1s, 2s, 4s
            print(f"[compress] {label} attempt {attempt + 1}/{retries} failed: "
                  f"{type(e).__name__}: {str(e)[:150]}"
                  f"{f' (retry in {wait}s)' if attempt < retries - 1 else ''}")
            if attempt < retries - 1:
                await asyncio.sleep(wait)

    return None


async def _llm_compress(
    old_messages: list[dict],
    model: str,
    api_key: str,
    api_base: Optional[str],
    fallback_model: Optional[str] = None,
    fallback_api_key: Optional[str] = None,
    fallback_base_url: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """
    Call compressor LLM with resilience: retry + circuit breaker + fallback endpoint.
    Returns (summary, model_used) or None on failure.
    """
    global _consecutive_failures, _circuit_open_until

    # Circuit breaker: skip if too many recent failures
    now = time.monotonic()
    async with _state_lock:
        if _circuit_open_until > now:
            remaining = int(_circuit_open_until - now)
            print(f"[compress] Circuit breaker OPEN — skipping LLM compressor ({remaining}s remaining)")
            return None  # Caller will use aggressive trimming fallback

    conversation_text = _serialize_messages_for_summary(old_messages)
    prompt = _COMPRESS_PROMPT.format(conversation=conversation_text)

    # Try primary compressor (3 retries)
    summary = await _llm_compress_single(prompt, model, api_key, api_base, label="primary")
    if summary:
        async with _state_lock:
            _consecutive_failures = 0
            # Track tokens spent after successful LLM compression
            # Simple estimation: prompt length / 3 (approximate tokens)
            compression_tokens = len(prompt) // 3
            _compression_tokens_spent[model] = (
                _compression_tokens_spent.get(model, 0) + compression_tokens
            )
        return summary, model

    # Try fallback compressor if configured (3 retries)
    if fallback_model and fallback_api_key:
        print(f"[compress] Primary failed, trying fallback ({fallback_model})")
        summary = await _llm_compress_single(
            prompt, fallback_model, fallback_api_key, fallback_base_url, label="fallback"
        )
        if summary:
            async with _state_lock:
                _consecutive_failures = 0
                # Track tokens spent after successful LLM compression (fallback)
                compression_tokens = len(prompt) // 3
                _compression_tokens_spent[fallback_model] = (
                    _compression_tokens_spent.get(fallback_model, 0) + compression_tokens
                )
            return summary, fallback_model

    # Both failed — update circuit breaker
    async with _state_lock:
        _consecutive_failures += 1
        if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
            _circuit_open_until = now + _CIRCUIT_BREAKER_COOLDOWN
            print(f"[compress] Circuit breaker OPENED after {_consecutive_failures} consecutive failures "
                  f"(cooldown {_CIRCUIT_BREAKER_COOLDOWN}s)")
        else:
            print(f"[compress] Compressor failed ({_consecutive_failures}/{_CIRCUIT_BREAKER_THRESHOLD} "
                  f"before circuit breaker)")

    return None


def _validate_tool_references(messages: list[dict]) -> bool:
    """Verify all tool_call_ids in role:tool have matching assistant tool_calls.

    Returns True if all references are valid, False if orphans exist.
    Only relevant for OpenAI-format messages (native tool models).
    For no-tools models, role:"tool" messages don't exist so this is a no-op.
    """
    available_ids = set()
    for msg in messages:
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            for tc in msg.get("tool_calls", []):
                tid = tc.get("id", "") if isinstance(tc, dict) else ""
                if tid:
                    available_ids.add(tid)
    for msg in messages:
        if msg.get("role") == "tool":
            tid = msg.get("tool_call_id", "")
            if tid and tid != "unknown" and tid not in available_ids:
                return False
    return True


def _fix_orphan_tool_messages(messages: list[dict]) -> list[dict]:
    """Convert orphaned role:tool messages to role:user with text content.

    Safety net: if _find_safe_split_point missed an orphan (e.g. due to
    cache reuse), this converts dangling tool results to user messages
    so the API doesn't reject with error 2013.
    """
    available_ids = set()
    for msg in messages:
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            for tc in msg.get("tool_calls", []):
                tid = tc.get("id", "") if isinstance(tc, dict) else ""
                if tid:
                    available_ids.add(tid)
    result = []
    for msg in messages:
        if msg.get("role") == "tool":
            tid = msg.get("tool_call_id", "")
            if tid and tid != "unknown" and tid not in available_ids:
                result.append({
                    "role": "user",
                    "content": f"[Tool result for {tid}]: {msg.get('content', '')}",
                })
                continue
        result.append(msg)
    return result


_XML_REINFORCEMENT = (
    "[REMINDER] Tool format:\n"
    '<tool_call name="Read">\n<input>\n{"file_path": "/path"}\n</input>\n</tool_call>\n'
    "Parameters MUST be JSON inside <input> tags. "
    "NEVER use XML parameter tags like <file_path> or <content> or <parameter name=\"X\">. "
    "Use ONLY <tool_call> and <input> tags.\n\n"
)


def _needs_xml_reinforcement(system_msg: Optional[dict]) -> bool:
    """Check if system message contains XML tool prompt (needs reinforcement after compression)."""
    if not system_msg:
        return False
    content = system_msg.get("content", "")
    return "<tool_call" in content


def _reassemble_with_summary(
    system_msg: Optional[dict],
    summary: str,
    recent_messages: list[dict],
) -> list[dict]:
    """Reassemble messages with summary replacing old messages."""
    result: list[dict] = []
    if system_msg:
        result.append(system_msg)
    # Reinforce XML tool format after compression to prevent prompt dilution
    prefix = _XML_REINFORCEMENT if _needs_xml_reinforcement(system_msg) else ""
    result.append({
        "role": "user",
        "content": f"{prefix}[Previous conversation summary]\n{summary}",
    })
    result.append({
        "role": "assistant",
        "content": "Understood. I have the context from our previous conversation. Continuing.",
    })
    result.extend(recent_messages)
    # Safety net: validate no orphan tool references after reassembly
    if not _validate_tool_references(result):
        print("[compress] WARNING: orphan tool references detected after reassembly, fixing...")
        result = _fix_orphan_tool_messages(result)
    return result


def _reassemble_trimmed(
    system_msg: Optional[dict],
    recent_messages: list[dict],
) -> list[dict]:
    """Fallback: just keep system + recent, discard old."""
    result: list[dict] = []
    if system_msg:
        result.append(system_msg)
    # Reinforce XML tool format after trimming to prevent prompt dilution
    prefix = _XML_REINFORCEMENT if _needs_xml_reinforcement(system_msg) else ""
    result.append({
        "role": "user",
        "content": f"{prefix}[Earlier conversation context was removed to fit context window]",
    })
    result.append({
        "role": "assistant",
        "content": "Understood. Some earlier context was removed. I'll work with what's available.",
    })
    result.extend(recent_messages)
    # Safety net: validate no orphan tool references after reassembly
    if not _validate_tool_references(result):
        print("[compress] WARNING: orphan tool references detected after trimming, fixing...")
        result = _fix_orphan_tool_messages(result)
    return result

# ── Session management functions (Phase 3 Enhancement) ──

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


# =============================================================================
# Evidence Graph Persistence (Priority 4 — session-level grounding continuity)
# =============================================================================

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


# =============================================================================
# Multi-Hop Grounding Tracking (CAREFUL IMPLEMENTATION)
# =============================================================================

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


# =============================================================================
# Logging Helper
# =============================================================================

def log_compaction(event_type: str, session_id: str, model: str, **kwargs) -> None:
    """Log compression events for debugging."""
    metadata = ", ".join(f"{k}={v}" for k, v in kwargs.items())
    print(f"[compress] {event_type}: session={session_id[:8]}..., model={model}, {metadata}")
    print(f"[session] No expired sessions to clean up")
