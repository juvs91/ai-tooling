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

ADR-0032: session-state helpers (plan mode, deferred tools, quality history,
grounding graph, completion claims) used to live inline in this file. They
now live in `llm/session/*` and `utils/message_utils.py` — this module keeps
only the compression algorithm itself, and re-exports everything else below
so existing `from llm.compressor import ...` call sites keep working unchanged.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Optional

import litellm

from utils.metrics import metrics
from utils.utils import count_tokens_accurate  # toksum integration
from llm.session_state import extract_session_state, inject_state_into_system_prompt, SessionState, extract_todo_state

# ── Re-exports: session state, split out of this module (ADR-0032) ────────
from llm.session.store import _CompressionCache, _session_cache, _state_lock, _compute_prefix_hash, _CACHE_PREFIX_SIZE
from llm.session.plan_mode import (
    get_session_plan_mode,
    set_session_plan_mode,
    get_session_plan_mode_source,
    set_session_plan_mode_source,
    get_session_plan_mode_events,
)
from llm.session.completion_claims import append_session_completion_claim, get_session_completion_claims
from llm.session.generality_claims import append_session_generality_claim, get_session_generality_claims
from llm.session.state_assertion_cache import (
    append_session_assertion_event,
    get_session_assertion_events,
)
from llm.session.quality_history import get_session_quality_history, append_session_quality
from llm.session.deferred_tools_cache import get_session_deferred_tools, save_session_deferred_tools
from llm.session.grounding import (
    get_session_grounding_graph,
    extend_session_grounding_graph,
    get_session_read_files,
    get_grounding_state,
    _track_grounding_hop,
    _prune_grounding_graph,
)
from llm.session.lifecycle import get_or_create_session, update_session, cleanup_expired_sessions
from utils.message_utils import (
    estimate_tools_tokens,
    _find_safe_split_point,
    _serialize_messages_for_summary,
    _normalize_messages,
    _detect_tool_inflation,
    _split_conversation,
    _trim_by_token_budget,
    _enforce_message_cap,
    _validate_tool_references,
    _fix_orphan_tool_messages,
    _needs_xml_reinforcement,
)

__all__ = [
    "compress_messages_if_needed",
    "log_compaction",
    # re-exported session state (ADR-0032) — kept for external call sites
    "_CompressionCache",
    "_session_cache",
    "_state_lock",
    "get_session_plan_mode",
    "set_session_plan_mode",
    "get_session_plan_mode_source",
    "set_session_plan_mode_source",
    "get_session_plan_mode_events",
    "append_session_completion_claim",
    "get_session_completion_claims",
    "append_session_generality_claim",
    "get_session_generality_claims",
    "get_session_quality_history",
    "append_session_quality",
    "get_session_deferred_tools",
    "save_session_deferred_tools",
    "get_session_grounding_graph",
    "extend_session_grounding_graph",
    "get_session_read_files",
    "get_grounding_state",
    "_track_grounding_hop",
    "_prune_grounding_graph",
    "get_or_create_session",
    "update_session",
    "cleanup_expired_sessions",
    "estimate_tools_tokens",
    "_find_safe_split_point",
    "_validate_tool_references",
    "_fix_orphan_tool_messages",
]


# Circuit breaker state (module-level, persists across requests)
_consecutive_failures: int = 0
_circuit_open_until: float = 0.0
_CIRCUIT_BREAKER_THRESHOLD = 5   # failures before opening circuit
_CIRCUIT_BREAKER_COOLDOWN = 60.0  # seconds to skip compressor after circuit opens

# Compression token budget to limit DeepSeek calls per session
# Configurable via COMPRESSION_TOKEN_BUDGET env var. Default raised to 200k (was 50k) — a 80-msg
# session consumes ~30k tokens per compression call; 50k was exhausted in 2-3 successful calls.
_COMPRESSION_TOKEN_BUDGET = int(os.getenv("COMPRESSION_TOKEN_BUDGET", "200000"))
_compression_tokens_spent: dict[str, int] = {}  # session_id -> tokens spent


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


_XML_REINFORCEMENT = (
    "[REMINDER] Tool format:\n"
    '<tool_call name="Read">\n<input>\n{"file_path": "/path"}\n</input>\n</tool_call>\n'
    "Parameters MUST be JSON inside <input> tags. "
    "NEVER use XML parameter tags like <file_path> or <content> or <parameter name=\"X\">. "
    "Use ONLY <tool_call> and <input> tags.\n\n"
)


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

    now = time.monotonic()
    # Check budget AND circuit breaker under one lock.
    # Budget exhaustion is a graceful stop — NOT a failure, must NOT increment _consecutive_failures.
    async with _state_lock:
        primary_budget = _compression_tokens_spent.get(model, 0)
        if primary_budget > _COMPRESSION_TOKEN_BUDGET:
            print(f"[compress] session budget exhausted ({primary_budget} > {_COMPRESSION_TOKEN_BUDGET}) — "
                  f"trimming only (no circuit error)")
            return None  # Return without touching circuit breaker state
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


# =============================================================================
# Logging Helper
# =============================================================================

def log_compaction(event_type: str, session_id: str, model: str, **kwargs) -> None:
    """Log compression events for debugging."""
    metadata = ", ".join(f"{k}={v}" for k, v in kwargs.items())
    print(f"[compress] {event_type}: session={session_id[:8]}..., model={model}, {metadata}")
    print(f"[session] No expired sessions to clean up")
