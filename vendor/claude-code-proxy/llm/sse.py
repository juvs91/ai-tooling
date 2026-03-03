# llm/sse.py
"""SSE event builders for the Anthropic streaming protocol.

Each function returns a fully-formatted SSE string ready to ``yield``
from an async generator.  This module is the **single source of truth**
for the wire format, keeping streaming.py free of JSON boilerplate.
"""
from __future__ import annotations

import json
from typing import Any


# ── Low-level helper ─────────────────────────────────────────────────

def sse(payload: dict) -> str:
    """Format *payload* as an SSE ``data:`` line."""
    return f"data: {json.dumps(payload)}\n\n"


def sse_event(event_name: str, payload: dict) -> str:
    """Format *payload* as a named SSE event (``event:`` + ``data:``)."""
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


# ── Protocol events ──────────────────────────────────────────────────

def message_start(msg_id: str, model: str, input_tokens: int = 0) -> str:
    """``message_start`` — opens the Anthropic SSE stream."""
    return sse_event("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    })


def content_block_start_text(index: int) -> str:
    """Open a new ``text`` content block."""
    return sse_event("content_block_start", {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "text", "text": ""},
    })


def content_block_start_tool(index: int, tool_id: str, name: str) -> str:
    """Open a new ``tool_use`` content block."""
    return sse_event("content_block_start", {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}},
    })


def text_delta(index: int, text: str) -> str:
    """Emit a ``text_delta`` inside a text content block."""
    return sse_event("content_block_delta", {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    })


def input_json_delta(index: int, partial_json: str) -> str:
    """Emit an ``input_json_delta`` inside a tool_use content block."""
    return sse_event("content_block_delta", {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "input_json_delta", "partial_json": partial_json},
    })


def content_block_stop(index: int) -> str:
    """Close a content block."""
    return sse_event("content_block_stop", {
        "type": "content_block_stop",
        "index": index,
    })


def message_delta(stop_reason: str, output_tokens: int) -> str:
    """``message_delta`` — signals stop_reason and final usage."""
    return sse_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })


def message_stop() -> str:
    """``message_stop`` — end of message."""
    return sse_event("message_stop", {"type": "message_stop"})


def ping() -> str:
    """Keep-alive ping."""
    return sse_event("ping", {"type": "ping"})


def done() -> str:
    """Final ``[DONE]`` sentinel (OpenAI convention forwarded by some clients)."""
    return "data: [DONE]\n\n"


def response_to_sse_events(response, model: str, input_tokens: int = 0):
    """Convert a MessagesResponse to SSE event strings.

    Yields SSE event strings in the correct Anthropic streaming order.
    """
    import uuid
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    yield message_start(msg_id, model, input_tokens)

    for idx, block in enumerate(response.content):
        if block.type == "text":
            yield content_block_start_text(idx)
            if block.text:
                yield text_delta(idx, block.text)
            yield content_block_stop(idx)
        elif block.type == "tool_use":
            yield content_block_start_tool(idx, block.id, block.name)
            yield input_json_delta(idx, "")
            try:
                args_json = json.dumps(block.input, ensure_ascii=False)
            except (TypeError, ValueError):
                args_json = json.dumps({"raw": str(block.input)})
            yield input_json_delta(idx, args_json)
            yield content_block_stop(idx)
        elif block.type in ("thinking", "redacted_thinking"):
            # Thinking blocks are internal model reasoning — skip in SSE output
            # (Claude Code doesn't expect these and they can crash its SSE parser)
            pass

    stop_reason = response.stop_reason or "end_turn"
    output_tokens = response.usage.output_tokens if response.usage else 0
    yield message_delta(stop_reason, output_tokens)

    yield message_stop()
    yield done()
