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
import time
from dataclasses import dataclass
from typing import Any, Optional

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

@dataclass
class _CompressionCache:
    summary: str              # The compressed summary text
    old_msg_count: int        # How many old messages were compressed
    timestamp: float          # time.monotonic() when cached
    prefix_hash: str          # Truncated SHA256 of first N old messages (session identity)

_compression_cache: Optional[_CompressionCache] = None
_CACHE_TTL = 300.0           # 5 minutes — covers a typical CC session
_CACHE_MSG_TOLERANCE = 100   # Reuse if ≤100 new old messages since last compression
_CACHE_PREFIX_SIZE = 20      # Hash first 20 messages for session identity


def _compute_prefix_hash(messages: list[dict], n: int = _CACHE_PREFIX_SIZE) -> str:
    """Hash the first N messages to identify the conversation session."""
    prefix = messages[:n]
    raw = json.dumps(prefix, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


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


async def compress_messages_if_needed(
    messages: list[dict],
    model_context_window: int,
    compressor_model: str,
    compressor_api_key: str,
    compressor_base_url: Optional[str] = None,
    keep_recent: int = 15,
    trigger_ratio: float = 0.85,
    tools_overhead_tokens: int = 0,
    target_model: str = "",
    fallback_model: Optional[str] = None,
    fallback_api_key: Optional[str] = None,
    fallback_base_url: Optional[str] = None,
) -> tuple[list[dict], bool]:
    """
    Compress conversation if it exceeds the model's context window.

    Args:
        messages: OpenAI-format messages (already converted from Anthropic)
        model_context_window: Target model's context window in tokens
        compressor_model: LiteLLM model string for the compressor (e.g. "openai/glm-4.7-flash")
        compressor_api_key: API key for the compressor
        compressor_base_url: Optional base URL for the compressor
        keep_recent: Number of recent messages to keep intact
        trigger_ratio: Compress when estimated tokens > ratio * window (default 0.85)
        tools_overhead_tokens: Extra tokens from tool definitions (not in messages)
        target_model: LiteLLM model string for the target model (used for accurate token counting)
        fallback_model: Optional fallback compressor model (tried if primary fails)
        fallback_api_key: Optional fallback compressor API key
        fallback_base_url: Optional fallback compressor base URL

    Returns:
        (messages, was_compressed) — compressed messages and whether compression happened
    """
    if model_context_window <= 0 or not compressor_model or not compressor_api_key:
        return messages, False

    estimated_tokens = _count_message_tokens(messages, model=target_model) + tools_overhead_tokens
    threshold = int(trigger_ratio * model_context_window)

    print(f"[compress] Check: tokens={estimated_tokens} (tools_overhead={tools_overhead_tokens}) "
          f"threshold={threshold} (window={model_context_window} × ratio={trigger_ratio}) "
          f"model={target_model}")

    if estimated_tokens <= threshold:
        return messages, False

    # Separate system, old, and recent messages
    system_msg = None
    conversation = messages
    if messages and messages[0].get("role") == "system":
        system_msg = messages[0]
        conversation = messages[1:]

    # Not enough messages to compress
    if len(conversation) <= keep_recent + 2:
        print(f"[compress] Skipped: only {len(conversation)} msgs (need > {keep_recent + 2})")
        return messages, False

    split_point = _find_safe_split_point(conversation, keep_recent)
    old_messages = conversation[:split_point]
    recent_messages = conversation[split_point:]

    # Nothing meaningful to compress
    if len(old_messages) < 3:
        print(f"[compress] Skipped: only {len(old_messages)} old msgs (need >= 3)")
        return messages, False

    print(f"[compress] Triggered: {estimated_tokens} tokens > {threshold} threshold. "
          f"Compressing {len(old_messages)} old messages, keeping {len(recent_messages)} recent. "
          f"compressor={compressor_model}")

    # ── Check compression cache before calling LLM ──
    global _compression_cache
    prefix_hash = _compute_prefix_hash(old_messages)
    now = time.monotonic()

    async with _state_lock:
        if (_compression_cache is not None
                and _compression_cache.prefix_hash == prefix_hash
                and (now - _compression_cache.timestamp) < _CACHE_TTL
                and (len(old_messages) - _compression_cache.old_msg_count) <= _CACHE_MSG_TOLERANCE):
            # Cache hit — reuse previous summary, skip the LLM call
            metrics.compression_cache_hits += 1
            age = int(now - _compression_cache.timestamp)
            delta = len(old_messages) - _compression_cache.old_msg_count
            print(f"[compress] Cache HIT: reusing summary "
                  f"(cached {_compression_cache.old_msg_count} msgs, now {len(old_messages)} msgs, "
                  f"delta={delta}, age={age}s)")
            cached_summary = _compression_cache.summary
        else:
            cached_summary = None
            metrics.compression_cache_misses += 1
            reason = "no cache" if _compression_cache is None else (
                "prefix mismatch" if _compression_cache.prefix_hash != prefix_hash else
                "expired" if (now - _compression_cache.timestamp) >= _CACHE_TTL else
                f"delta {len(old_messages) - _compression_cache.old_msg_count} > {_CACHE_MSG_TOLERANCE}"
            )
            print(f"[compress] Cache MISS ({reason}): compressing fresh (prefix_hash={prefix_hash})")

    if cached_summary is not None:
        compressed = _reassemble_with_summary(system_msg, cached_summary, recent_messages)
        new_tokens = _count_message_tokens(compressed, model=target_model)
        print(f"[compress] Success (cached): {estimated_tokens} → {new_tokens} tokens "
              f"(saved {estimated_tokens - new_tokens})")
        return compressed, True

    # Try LLM compression (retry + circuit breaker + fallback)
    result = await _llm_compress(
        old_messages, compressor_model, compressor_api_key, compressor_base_url,
        fallback_model=fallback_model,
        fallback_api_key=fallback_api_key,
        fallback_base_url=fallback_base_url,
    )

    if result:
        summary, model_used = result
        # Store in cache for next request
        # Recalculate timestamp inside lock to avoid race condition where
        # 'now' (from line 257) becomes stale after the long LLM call (2-5s)
        async with _state_lock:
            cache_timestamp = time.monotonic()
            _compression_cache = _CompressionCache(
                summary=summary,
                old_msg_count=len(old_messages),
                timestamp=cache_timestamp,
                prefix_hash=prefix_hash,
            )
        compressed = _reassemble_with_summary(system_msg, summary, recent_messages)
        new_tokens = _count_message_tokens(compressed, model=target_model)
        print(f"[compress] Success ({model_used}): {estimated_tokens} → {new_tokens} tokens "
              f"(saved {estimated_tokens - new_tokens})")
        return compressed, True

    # Fallback: aggressive trimming — keep only 5 most recent messages
    # (not keep_recent=15) to ensure we stay well under context window.
    # CC resends full conversation next turn, so trimming more aggressively
    # prevents the regrowth cycle where tokens grow 12K→218K.
    aggressive_keep = min(5, len(recent_messages))
    aggressive_recent = recent_messages[-aggressive_keep:] if aggressive_keep > 0 else recent_messages
    print(f"[compress] LLM compression failed, falling back to aggressive trimming "
          f"(keeping {len(aggressive_recent)} of {len(conversation)} messages)")
    trimmed = _reassemble_trimmed(system_msg, aggressive_recent)
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
