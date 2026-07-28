# utils/message_utils.py
"""
Pure message-manipulation utilities used by the compressor pipeline.

Split out of llm/compressor.py — ADR-0032. None of these touch session-cache
state (_session_cache/_state_lock) — verified by grep over the original
compressor.py before the split — so they live here as plain utilities.
"""
from __future__ import annotations

import json
from typing import Optional

from utils.metrics import metrics
from utils.utils import count_tokens_accurate


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


def _needs_xml_reinforcement(system_msg: Optional[dict]) -> bool:
    """Check if system message contains XML tool prompt (needs reinforcement after compression)."""
    if not system_msg:
        return False
    content = system_msg.get("content", "")
    return "<tool_call" in content
