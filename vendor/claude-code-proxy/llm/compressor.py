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

from litellm import token_counter
from utils.metrics import metrics


# Circuit breaker state (module-level, persists across requests)
_consecutive_failures: int = 0
_circuit_open_until: float = 0.0
_CIRCUIT_BREAKER_THRESHOLD = 5   # failures before opening circuit
_CIRCUIT_BREAKER_COOLDOWN = 60.0  # seconds to skip compressor after circuit opens

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
    """Persist _session_cache to JSON. Must be called within _state_lock."""
    try:
        data = {
            str(sid) if sid is not None else "__default__": {
                "summary": c.summary,
                "old_msg_count": c.old_msg_count,
                "timestamp": c.timestamp,
                "session_id": c.session_id,
            }
            for sid, c in _session_cache.items()
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
            )
            loaded += 1
        if loaded:
            print(f"[session] Restored {loaded} session(s) from {_SESSION_CACHE_FILE}")
    except Exception as e:
        print(f"[session] Failed to load cache from disk: {e}")


_load_session_cache_from_disk()  # restore sessions from previous proxy run


_COMPRESS_PROMPT = (
    "You are a conversation summarizer. Summarize the following conversation context concisely.\n\n"
    "RULES:\n"
    "- PRESERVE: file paths, tool names, function names, error messages, key decisions, code snippets\n"
    "- REMOVE: verbose tool outputs, repetitive explanations, intermediate reasoning\n"
    "- Keep the summary under 2000 tokens\n"
    "- Use bullet points for clarity\n"
    "- Include any unresolved issues or pending tasks\n\n"
    "Conversation to summarize:\n{conversation}\n\n"
    "Concise summary:"
)


def _count_message_tokens(messages: list[dict], model: str = "") -> int:
    """
    Count tokens using litellm's tokenizer (deterministic, local, no API call).
    Falls back to chars/3 heuristic if tokenizer fails.
    """
    if not messages:
        return 0

    try:
        count = token_counter(model=model, messages=messages)
        return max(1, count)
    except Exception as e:
        # Fallback: chars/3 (better than chars/4 for JSON/XML-heavy content)
        print(f"[compress] token_counter failed ({type(e).__name__}), using chars/3 fallback")
        total = 0
        for msg in messages:
            content = msg.get("content", "") or ""
            total += len(content) // 3
        return max(1, total)


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
) -> tuple[list[dict], list[dict]]:
    """
    Split conversation into old (to be summarized) and recent (to keep intact).

    All thresholds are calculated dynamically from config ratios - no magic numbers.
    Uses message count (not just tokens) to ensure compression triggers early.
    """
    # Calculate dynamic thresholds from config ratios
    summary_trigger_tokens = int(model_context_window * summary_trigger_ratio)
    recent_window_tokens = int(model_context_window * recent_window_ratio)

    # Use message threshold for triggering (not token-based)
    # This ensures compression starts at 20 messages, not 400
    recent_window_msgs = max(10, recent_window_tokens // 300)  # Keep at least 10 recent

    # Not enough messages to split
    if len(messages) <= message_threshold + recent_window_msgs:
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
    current_tokens = _count_message_tokens(messages, model=target_model)
    if current_tokens <= max_tokens:
        return messages

    print(f"[compress] Token budget exceeded: {current_tokens} > {max_tokens}, trimming...")

    # Remove oldest messages until we fit the budget
    trimmed = messages.copy()
    while len(trimmed) > 10:  # Keep at least 10 messages minimum
        current_tokens = _count_message_tokens(trimmed, model=target_model)
        if current_tokens <= max_tokens:
            break
        trimmed.pop(0)

    new_tokens = _count_message_tokens(trimmed, model=target_model)
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

    # Calculate dynamic limits from config ratios
    max_messages = int(model_context_window * cfg.max_messages_ratio // 300)
    max_tokens = int(model_context_window * cfg.max_tokens_ratio)
    summary_trigger_tokens = int(model_context_window * cfg.summary_trigger_ratio)
    recent_window_tokens = int(model_context_window * cfg.recent_window_ratio)

    print(f"[compress] Dynamic limits for model (context_window={model_context_window}): "
          f"max_messages={max_messages}, max_tokens={max_tokens}, "
          f"summary_trigger={summary_trigger_tokens} tokens, recent_window={recent_window_tokens} tokens")

    # Step 1: Normalize messages
    messages = _normalize_messages(messages)

    # Step 2: Detect tool inflation
    if _detect_tool_inflation(messages, cfg.tool_inflation_threshold):
        print(f"[compress] Tool inflation detected: >{cfg.tool_inflation_threshold} tool messages")

    estimated_tokens = _count_message_tokens(messages, model=target_model) + tools_overhead_tokens
    threshold = int(cfg.trigger_ratio * model_context_window)

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
    effective_session_id = session_id if session_id else str(
        uuid.uuid5(uuid.NAMESPACE_OID, _compute_prefix_hash(old_messages, _CACHE_PREFIX_SIZE))
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

    if cached_summary is not None:
        compressed = _reassemble_with_summary(system_msg, cached_summary, recent_messages)
        new_tokens = _count_message_tokens(compressed, model=target_model)
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

        # Step 5: Enforce token budget
        merged = _trim_by_token_budget(merged, max_tokens, target_model)

        # Step 6: Enforce message cap
        merged = _enforce_message_cap(merged, max_messages)

        new_tokens = _count_message_tokens(merged, model=target_model)
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

    new_tokens = _count_message_tokens(trimmed, model=target_model)
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
    import litellm

    for attempt in range(retries):
        print(f"[compress] {label} calling {model} (attempt {attempt + 1}/{retries})")
        try:
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
        _session_cache[session_id] = _CompressionCache(
            session_id=session_id,
            summary=summary,
            old_msg_count=old_count,
            timestamp=now
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
            print(f"[session] No expired sessions to clean up")
