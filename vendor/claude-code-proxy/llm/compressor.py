# llm/compressor.py
"""
Context compression for models with limited context windows.

When a conversation exceeds the model's context window, this module:
  1. Keeps system prompt + recent messages intact
  2. Summarizes older messages using a cheap LLM call
  3. Reassembles: [system] + [summary] + [recent messages]
  4. Falls back to simple trimming if the compressor fails
"""
from __future__ import annotations

import json
from typing import Any, Optional

from litellm import token_counter


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
    return total_chars // 4


def _serialize_messages_for_summary(messages: list[dict], max_chars: int = 50000) -> str:
    """Serialize messages to text for the compressor, truncating large outputs."""
    lines = []
    chars = 0
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "") or ""
        # Truncate individual messages that are too long (e.g. large tool outputs)
        if len(content) > 3000:
            content = content[:1500] + "\n...[truncated]...\n" + content[-500:]
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
) -> tuple[list[dict], bool]:
    """
    Compress conversation if it exceeds the model's context window.

    Args:
        messages: OpenAI-format messages (already converted from Anthropic)
        model_context_window: Target model's context window in tokens
        compressor_model: LiteLLM model string for the compressor (e.g. "openai/deepseek-chat")
        compressor_api_key: API key for the compressor
        compressor_base_url: Optional base URL for the compressor
        keep_recent: Number of recent messages to keep intact
        trigger_ratio: Compress when estimated tokens > ratio * window (default 0.85)
        tools_overhead_tokens: Extra tokens from tool definitions (not in messages)
        target_model: LiteLLM model string for the target model (used for accurate token counting)

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

    old_messages = conversation[:-keep_recent]
    recent_messages = conversation[-keep_recent:]

    # Nothing meaningful to compress
    if len(old_messages) < 3:
        print(f"[compress] Skipped: only {len(old_messages)} old msgs (need >= 3)")
        return messages, False

    print(f"[compress] Triggered: {estimated_tokens} tokens > {threshold} threshold. "
          f"Compressing {len(old_messages)} old messages, keeping {len(recent_messages)} recent.")

    # Try LLM compression
    summary = await _llm_compress(old_messages, compressor_model, compressor_api_key, compressor_base_url)

    if summary:
        compressed = _reassemble_with_summary(system_msg, summary, recent_messages)
        new_tokens = _count_message_tokens(compressed, model=target_model)
        print(f"[compress] Success: {estimated_tokens} → {new_tokens} tokens "
              f"(saved {estimated_tokens - new_tokens})")
        return compressed, True

    # Fallback: simple trimming (discard old, keep recent)
    print(f"[compress] LLM compression failed, falling back to trimming")
    trimmed = _reassemble_trimmed(system_msg, recent_messages)
    new_tokens = _count_message_tokens(trimmed, model=target_model)
    print(f"[compress] Trimmed: {estimated_tokens} → {new_tokens} tokens")
    return trimmed, True


async def _llm_compress(
    old_messages: list[dict],
    model: str,
    api_key: str,
    api_base: Optional[str],
) -> Optional[str]:
    """Call compressor LLM to summarize old messages. Returns summary or None on failure."""
    import litellm

    conversation_text = _serialize_messages_for_summary(old_messages)
    prompt = _COMPRESS_PROMPT.format(conversation=conversation_text)

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
            print(f"[compress] Compressor returned too-short summary ({len(summary)} chars)")
            return None

        return summary

    except Exception as e:
        print(f"[compress] Compressor LLM failed: {type(e).__name__}: {str(e)[:200]}")
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
    result.append({
        "role": "user",
        "content": f"[Previous conversation summary]\n{summary}",
    })
    result.append({
        "role": "assistant",
        "content": "Understood. I have the context from our previous conversation. Continuing.",
    })
    result.extend(recent_messages)
    return result


def _reassemble_trimmed(
    system_msg: Optional[dict],
    recent_messages: list[dict],
) -> list[dict]:
    """Fallback: just keep system + recent, discard old."""
    result: list[dict] = []
    if system_msg:
        result.append(system_msg)
    result.append({
        "role": "user",
        "content": "[Earlier conversation context was removed to fit context window]",
    })
    result.append({
        "role": "assistant",
        "content": "Understood. Some earlier context was removed. I'll work with what's available.",
    })
    result.extend(recent_messages)
    return result
