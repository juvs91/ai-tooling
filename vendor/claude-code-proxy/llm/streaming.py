# llm/streaming.py
from __future__ import annotations

import json
import uuid


async def handle_streaming(response_generator, original_request):
    """
    Convierte el stream de LiteLLM (delta OpenAI-ish) a SSE Anthropic-like.
    """
    try:
        message_id = f"msg_{uuid.uuid4().hex[:24]}"

        message_data = {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": original_request.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                },
            },
        }
        yield f"event: message_start\ndata: {json.dumps(message_data)}\n\n"
        yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
        yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"

        tool_index = None
        accumulated_text = ""
        text_sent = False
        text_block_closed = False
        input_tokens = 0
        output_tokens = 0
        has_sent_stop_reason = False
        last_tool_index = 0

        async for chunk in response_generator:
            try:
                # usage if present
                if hasattr(chunk, "usage") and chunk.usage is not None:
                    if hasattr(chunk.usage, "prompt_tokens"):
                        input_tokens = chunk.usage.prompt_tokens
                    if hasattr(chunk.usage, "completion_tokens"):
                        output_tokens = chunk.usage.completion_tokens

                if not hasattr(chunk, "choices") or not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None) or getattr(choice, "message", None) or {}
                finish_reason = getattr(choice, "finish_reason", None)

                # text delta
                delta_content = getattr(delta, "content", None) if not isinstance(delta, dict) else delta.get("content")
                if delta_content:
                    accumulated_text += delta_content
                    if tool_index is None and not text_block_closed:
                        text_sent = True
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': delta_content}})}\n\n"

                # tool_calls delta
                delta_tool_calls = getattr(delta, "tool_calls", None) if not isinstance(delta, dict) else delta.get("tool_calls")
                if delta_tool_calls:
                    if tool_index is None:
                        # close text block before tools
                        if text_sent and not text_block_closed:
                            text_block_closed = True
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                        elif accumulated_text and not text_sent and not text_block_closed:
                            text_sent = True
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': accumulated_text}})}\n\n"
                            text_block_closed = True
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                        elif not text_block_closed:
                            text_block_closed = True
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

                    if not isinstance(delta_tool_calls, list):
                        delta_tool_calls = [delta_tool_calls]

                    for tool_call in delta_tool_calls:
                        current_index = None
                        if isinstance(tool_call, dict) and "index" in tool_call:
                            current_index = tool_call["index"]
                        elif hasattr(tool_call, "index"):
                            current_index = tool_call.index
                        else:
                            current_index = 0

                        # new tool block
                        if tool_index is None or current_index != tool_index:
                            tool_index = current_index
                            last_tool_index += 1
                            anthropic_tool_index = last_tool_index

                            if isinstance(tool_call, dict):
                                function = tool_call.get("function", {}) or {}
                                name = function.get("name", "")
                                tool_id = tool_call.get("id", f"toolu_{uuid.uuid4().hex[:24]}")
                            else:
                                function = getattr(tool_call, "function", None)
                                name = getattr(function, "name", "") if function else ""
                                tool_id = getattr(tool_call, "id", f"toolu_{uuid.uuid4().hex[:24]}")

                            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': anthropic_tool_index, 'content_block': {'type': 'tool_use', 'id': tool_id, 'name': name, 'input': {}}})}\n\n"

                        # arguments delta
                        if isinstance(tool_call, dict):
                            function = tool_call.get("function", {}) or {}
                            arguments = function.get("arguments", "")
                        else:
                            function = getattr(tool_call, "function", None)
                            arguments = getattr(function, "arguments", "") if function else ""

                        if arguments:
                            # send raw partial_json (Anthropic-style)
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': last_tool_index, 'delta': {'type': 'input_json_delta', 'partial_json': arguments}})}\n\n"

                # finish
                if finish_reason and not has_sent_stop_reason:
                    has_sent_stop_reason = True

                    # close tool blocks
                    if tool_index is not None:
                        for i in range(1, last_tool_index + 1):
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"

                    # close text block if still open
                    if not text_block_closed:
                        if accumulated_text and not text_sent:
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': accumulated_text}})}\n\n"
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

                    stop_reason = "end_turn"
                    if finish_reason == "length":
                        stop_reason = "max_tokens"
                    elif finish_reason == "tool_calls":
                        stop_reason = "tool_use"

                    yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': output_tokens}})}\n\n"
                    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

            except Exception:
                # si un chunk falla, seguimos (no matamos el stream)
                continue

        # fallback close if never finished
        if not has_sent_stop_reason:
            if tool_index is not None:
                for i in range(1, last_tool_index + 1):
                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"
            if not text_block_closed:
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': output_tokens}})}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
            yield "data: [DONE]\n\n"

    except Exception:
        # hard fail streaming: emit error-ish stop
        yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'error', 'stop_sequence': None}, 'usage': {'output_tokens': 0}})}\n\n"
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
        yield "data: [DONE]\n\n"
