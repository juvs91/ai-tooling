# llm/streaming.py
from __future__ import annotations

import json
import uuid

from utils.utils import scale_tokens
from json_repair import repair_json
import os
from llm.tool_prompting import is_no_tools_model, XmlToolBuffer, recover_incomplete_tool_call, extract_tool_calls_from_text


def _close_json_brackets(text: str) -> str:
    """Compute minimal suffix to close all open brackets/braces/strings in JSON text."""
    in_string = False
    escape_next = False
    stack = []

    for char in text:
        if escape_next:
            escape_next = False
            continue
        if char == '\\' and in_string:
            escape_next = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char in ('{', '['):
                stack.append(char)
            elif char == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif char == ']' and stack and stack[-1] == '[':
                stack.pop()

    suffix = ''
    if in_string:
        suffix += '"'
    for bracket in reversed(stack):
        suffix += '}' if bracket == '{' else ']'
    return suffix


def _has_truncation_artifacts(json_str: str) -> bool:
    """Detect if repaired JSON has truncation artifacts (all-empty string values in objects).

    When json_repair closes truncated strings, it produces "" for all values.
    A single empty string might be intentional, but ALL strings empty = truncated.
    """
    try:
        parsed = json.loads(json_str)
    except Exception:
        return True

    def _check(obj) -> bool:
        if isinstance(obj, dict):
            string_vals = [v for v in obj.values() if isinstance(v, str)]
            if len(string_vals) >= 2 and all(v == '' for v in string_vals):
                return True
            for v in obj.values():
                if isinstance(v, (dict, list)) and _check(v):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)) and _check(item):
                    return True
        return False

    return _check(parsed)


def _compute_repair_suffix(accumulated: str, tool_index: int) -> str | None:
    """Try to repair truncated JSON and return the suffix to append, or None."""
    if not accumulated:
        return None
    try:
        json.loads(accumulated)
        return None  # already valid
    except json.JSONDecodeError:
        pass

    # Strategy 1: json_repair library (only safe if it only appends to our prefix)
    try:
        repaired_str = repair_json(accumulated)
        # CRITICAL: repair_json may MODIFY middle characters (e.g. adding closing quotes).
        # Only use suffix approach if repaired string starts with our exact accumulated text.
        if repaired_str.startswith(accumulated):
            suffix = repaired_str[len(accumulated):]
            if suffix:
                json.loads(accumulated + suffix)  # validate
                print(f"[json-repair] Streaming: library suffix for tool index {tool_index} ({len(suffix)} chars)")
                return suffix
    except Exception:
        pass

    # Strategy 2: Manual bracket/brace/string closer (always safe — only appends)
    suffix = _close_json_brackets(accumulated)
    if suffix:
        try:
            json.loads(accumulated + suffix)
            print(f"[json-repair] Streaming: bracket-close suffix for tool index {tool_index} ({len(suffix)} chars)")
            return suffix
        except Exception:
            pass

    print(f"[json-repair] Streaming: ALL repair strategies failed for tool index {tool_index} ({len(accumulated)} chars)")
    return None


def _warn_empty_tool_values(name: str, input_dict: dict) -> None:
    """Log a warning if tool arguments contain suspiciously empty values (model quality issue)."""
    if name == "TodoWrite":
        todos = input_dict.get("todos", [])
        if todos and isinstance(todos, list):
            empty_count = sum(1 for t in todos if isinstance(t, dict) and t.get("content", "") == "")
            if empty_count > 0:
                print(f"[quality] WARNING: TodoWrite has {empty_count}/{len(todos)} todos with empty 'content' — model likely truncated or hallucinated empty values")
    elif name == "Write":
        if input_dict.get("content", None) == "":
            print(f"[quality] WARNING: Write tool has empty 'content' — model likely truncated")


def _emit_tool_use_block(name: str, input_dict: dict, block_index: int) -> list[str]:
    """Generate SSE events for a single tool_use block (matches Anthropic SSE spec)."""
    _warn_empty_tool_values(name, input_dict)
    tool_id = f"toolu_{uuid.uuid4().hex[:24]}"
    args_json = json.dumps(input_dict, ensure_ascii=False)
    return [
        f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': block_index, 'content_block': {'type': 'tool_use', 'id': tool_id, 'name': name, 'input': {}}})}\n\n",
        # Initial empty partial_json delta — required by Anthropic SSE protocol.
        # CC uses this as initialization signal for its JSON accumulator.
        f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_index, 'delta': {'type': 'input_json_delta', 'partial_json': ''}})}\n\n",
        f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': block_index, 'delta': {'type': 'input_json_delta', 'partial_json': args_json}})}\n\n",
        f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': block_index})}\n\n",
    ]


async def handle_streaming(response_generator, original_request, model_context_window: int = 0):
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
                "model": getattr(original_request, "original_model", None) or original_request.model,
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
        output_tokens = 0
        has_sent_stop_reason = False
        last_tool_index = 0
        tool_args_buffer: dict[int, str] = {}  # tool index -> accumulated args JSON

        # XML tool simulation state
        no_tools_mode = is_no_tools_model(original_request.model)
        xml_tool_buffer = XmlToolBuffer() if no_tools_mode else None
        has_xml_tool_calls = False
        reasoning_buffer = ""  # Buffer reasoning_content in no-tools mode; emit only if no tool calls
        print(f"[streaming] INIT: model={original_request.model} no_tools_mode={no_tools_mode} xml_buffer={'YES' if xml_tool_buffer else 'NO'}", flush=True)

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

                # reasoning_content delta (deepseek-reasoner)
                delta_reasoning = getattr(delta, "reasoning_content", None) if not isinstance(delta, dict) else delta.get("reasoning_content")
                if delta_reasoning:
                    if no_tools_mode:
                        # Buffer reasoning in no-tools mode; only emit if no tool calls at stream end.
                        # Reasoning (5-15K tokens) before tool_use blocks crashes CC's SSE parser.
                        reasoning_buffer += delta_reasoning
                    elif tool_index is None and not text_block_closed:
                        text_sent = True
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': delta_reasoning}})}\n\n"

                # text delta
                delta_content = getattr(delta, "content", None) if not isinstance(delta, dict) else delta.get("content")
                if delta_content:
                    if xml_tool_buffer:
                        # Process through XML buffer state machine
                        for segment in xml_tool_buffer.feed(delta_content):
                            if segment["type"] == "text":
                                accumulated_text += segment["text"]
                                if tool_index is None and not text_block_closed:
                                    text_sent = True
                                    yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': segment['text']}})}\n\n"
                            elif segment["type"] == "tool_call":
                                has_xml_tool_calls = True
                                # Close text block if still open
                                if not text_block_closed:
                                    text_block_closed = True
                                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                                # Emit tool_use block
                                last_tool_index += 1
                                print(f"[streaming] XML tool_use emitted: name={segment['name']} index={last_tool_index}", flush=True)
                                for event in _emit_tool_use_block(segment["name"], segment["input"], last_tool_index):
                                    yield event
                    else:
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
                                raw_id = tool_call.get("id", "")
                                tool_id = raw_id if raw_id.startswith("toolu_") else f"toolu_{uuid.uuid4().hex[:24]}"
                            else:
                                function = getattr(tool_call, "function", None)
                                name = getattr(function, "name", "") if function else ""
                                raw_id = getattr(tool_call, "id", "") or ""
                                tool_id = raw_id if raw_id.startswith("toolu_") else f"toolu_{uuid.uuid4().hex[:24]}"

                            if not name:
                                print(f"[streaming] WARNING: Skipping tool_call with empty name (index={current_index})")
                                continue

                            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': anthropic_tool_index, 'content_block': {'type': 'tool_use', 'id': tool_id, 'name': name, 'input': {}}})}\n\n"
                            # Emit initial empty partial_json delta (matches official Anthropic SSE format)
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': anthropic_tool_index, 'delta': {'type': 'input_json_delta', 'partial_json': ''}})}\n\n"

                        # arguments delta
                        if isinstance(tool_call, dict):
                            function = tool_call.get("function", {}) or {}
                            arguments = function.get("arguments", "")
                        else:
                            function = getattr(tool_call, "function", None)
                            arguments = getattr(function, "arguments", "") if function else ""

                        if arguments:
                            # accumulate for post-hoc JSON repair
                            if last_tool_index not in tool_args_buffer:
                                tool_args_buffer[last_tool_index] = ""
                            tool_args_buffer[last_tool_index] += arguments
                            # send raw partial_json (Anthropic-style)
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': last_tool_index, 'delta': {'type': 'input_json_delta', 'partial_json': arguments}})}\n\n"

                # finish
                if finish_reason and not has_sent_stop_reason:
                    has_sent_stop_reason = True

                    # Flush XML tool buffer (remaining text/tool calls)
                    if xml_tool_buffer:
                        for segment in xml_tool_buffer.flush():
                            if segment["type"] == "text" and "<tool_call" in segment["text"]:
                                # Incomplete tool call — attempt recovery via compact retry
                                recovered = await recover_incomplete_tool_call(
                                    partial_xml=segment["text"],
                                    tools=getattr(original_request, "tools", None),
                                    model=os.environ.get("CLASSIFIER_MODEL", "openai/deepseek-chat"),
                                    api_key=os.environ.get("CLASSIFIER_API_KEY", ""),
                                    api_base=os.environ.get("CLASSIFIER_BASE_URL"),
                                )
                                if recovered:
                                    has_xml_tool_calls = True
                                    if not text_block_closed:
                                        text_block_closed = True
                                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                                    for tc in recovered:
                                        last_tool_index += 1
                                        for event in _emit_tool_use_block(tc["name"], tc["input"], last_tool_index):
                                            yield event
                                else:
                                    # Recovery failed — emit as text
                                    seg_text = segment["text"]
                                    if seg_text.strip():
                                        accumulated_text += seg_text
                                        if not text_block_closed:
                                            text_sent = True
                                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': seg_text}})}\n\n"
                            elif segment["type"] == "text":
                                seg_text = segment["text"]
                                if seg_text.strip():
                                    accumulated_text += seg_text
                                    if not text_block_closed:
                                        text_sent = True
                                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': seg_text}})}\n\n"
                            elif segment["type"] == "tool_call":
                                has_xml_tool_calls = True
                                if not text_block_closed:
                                    text_block_closed = True
                                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                                last_tool_index += 1
                                for event in _emit_tool_use_block(segment["name"], segment["input"], last_tool_index):
                                    yield event

                    # close tool blocks (with JSON repair + validation)
                    valid_tool_blocks = 0
                    if tool_index is not None:
                        for i in range(1, last_tool_index + 1):
                            accumulated = tool_args_buffer.get(i, "")
                            is_valid = False
                            was_repaired = False

                            suffix = _compute_repair_suffix(accumulated, i)
                            if suffix:
                                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': i, 'delta': {'type': 'input_json_delta', 'partial_json': suffix}})}\n\n"
                                accumulated += suffix
                                was_repaired = True

                            # Validate final JSON
                            if accumulated:
                                try:
                                    json.loads(accumulated)
                                    # Truncation artifact check: if we repaired AND response was
                                    # truncated (length), check for empty string values — sign that
                                    # json_repair closed strings prematurely (e.g. TodoWrite empty bullets)
                                    if was_repaired and finish_reason == "length" and _has_truncation_artifacts(accumulated):
                                        print(f"[streaming] WARNING: tool index {i} repair produced truncation artifacts (empty values), marking invalid")
                                        is_valid = False
                                    else:
                                        is_valid = True
                                except json.JSONDecodeError:
                                    print(f"[streaming] WARNING: tool index {i} has irrecoverable JSON ({len(accumulated)} chars)")
                            else:
                                is_valid = True  # empty args = valid {}

                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"
                            if is_valid:
                                valid_tool_blocks += 1

                    # Emit buffered reasoning ONLY when no tool calls (reasoning + tools crashes CC)
                    if reasoning_buffer and not has_xml_tool_calls and tool_index is None:
                        # deepseek-reasoner sometimes puts <tool_call> XML in reasoning_content instead of content
                        if "<tool_call" in reasoning_buffer:
                            reasoning_tools, reasoning_text = extract_tool_calls_from_text(reasoning_buffer)
                            if reasoning_tools:
                                has_xml_tool_calls = True
                                print(f"[streaming] Found {len(reasoning_tools)} tool call(s) in reasoning_content!", flush=True)
                                # Emit remaining text
                                if reasoning_text.strip() and not text_block_closed:
                                    text_sent = True
                                    yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': reasoning_text}})}\n\n"
                                # Close text block before tool_use
                                if not text_block_closed:
                                    text_block_closed = True
                                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                                # Emit tool_use blocks
                                for tc in reasoning_tools:
                                    last_tool_index += 1
                                    print(f"[streaming] XML tool_use from reasoning: name={tc['name']} index={last_tool_index}", flush=True)
                                    for event in _emit_tool_use_block(tc["name"], tc["input"], last_tool_index):
                                        yield event
                            else:
                                # <tool_call> found but regex didn't match — emit as text
                                if not text_block_closed:
                                    text_sent = True
                                    yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': reasoning_buffer}})}\n\n"
                        else:
                            if not text_block_closed:
                                text_sent = True
                                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': reasoning_buffer}})}\n\n"
                    elif reasoning_buffer and (has_xml_tool_calls or tool_index is not None):
                        print(f"[streaming] Suppressed {len(reasoning_buffer)} chars of reasoning_content (tool calls present)")

                    # close text block if still open
                    if not text_block_closed:
                        if accumulated_text and not text_sent:
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': accumulated_text}})}\n\n"
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

                    # stop_reason: tool_use ONLY if all native tool blocks have valid JSON
                    # Per Anthropic spec: stop_reason must be "tool_use" when tool_use blocks present
                    # BUT if finish_reason=length and JSON is corrupted, use max_tokens so CC doesn't execute garbage
                    total_native_tools = last_tool_index if tool_index is not None else 0
                    stop_reason = "end_turn"
                    if finish_reason == "length":
                        if (tool_index is not None or has_xml_tool_calls) and valid_tool_blocks == total_native_tools:
                            stop_reason = "tool_use"  # tools are valid, CC can execute them
                        else:
                            stop_reason = "max_tokens"  # corrupted or no tools
                    elif finish_reason == "tool_calls" or has_xml_tool_calls or tool_index is not None:
                        stop_reason = "tool_use"

                    print(f"[streaming] CLOSE: stop_reason={stop_reason} finish_reason={finish_reason} tool_index={tool_index} has_xml={has_xml_tool_calls} no_tools={no_tools_mode}", flush=True)
                    yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': scale_tokens(output_tokens, model_context_window)}})}\n\n"
                    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

            except Exception as e:
                print(f"[streaming] chunk error (skipped): {type(e).__name__}: {e}")
                continue

        # fallback close if never finished
        if not has_sent_stop_reason:
            # Flush XML buffer
            if xml_tool_buffer:
                for segment in xml_tool_buffer.flush():
                    if segment["type"] == "text" and "<tool_call" in segment["text"]:
                        # Incomplete tool call — attempt recovery
                        recovered = await recover_incomplete_tool_call(
                            partial_xml=segment["text"],
                            tools=getattr(original_request, "tools", None),
                            model=os.environ.get("CLASSIFIER_MODEL", "openai/deepseek-chat"),
                            api_key=os.environ.get("CLASSIFIER_API_KEY", ""),
                            api_base=os.environ.get("CLASSIFIER_BASE_URL"),
                        )
                        if recovered:
                            has_xml_tool_calls = True
                            if not text_block_closed:
                                text_block_closed = True
                                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                            for tc in recovered:
                                last_tool_index += 1
                                for event in _emit_tool_use_block(tc["name"], tc["input"], last_tool_index):
                                    yield event
                        elif segment["text"].strip():
                            if not text_block_closed:
                                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': segment['text']}})}\n\n"
                    elif segment["type"] == "text" and segment["text"].strip():
                        if not text_block_closed:
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': segment['text']}})}\n\n"
                    elif segment["type"] == "tool_call":
                        has_xml_tool_calls = True
                        if not text_block_closed:
                            text_block_closed = True
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                        last_tool_index += 1
                        for event in _emit_tool_use_block(segment["name"], segment["input"], last_tool_index):
                            yield event

            if tool_index is not None:
                for i in range(1, last_tool_index + 1):
                    suffix = _compute_repair_suffix(tool_args_buffer.get(i, ""), i)
                    if suffix:
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': i, 'delta': {'type': 'input_json_delta', 'partial_json': suffix}})}\n\n"
                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"

            # Emit buffered reasoning ONLY when no tool calls (fallback close path)
            if reasoning_buffer and not has_xml_tool_calls and tool_index is None:
                # deepseek-reasoner sometimes puts <tool_call> XML in reasoning_content instead of content
                if "<tool_call" in reasoning_buffer:
                    reasoning_tools, reasoning_text = extract_tool_calls_from_text(reasoning_buffer)
                    if reasoning_tools:
                        has_xml_tool_calls = True
                        print(f"[streaming] Found {len(reasoning_tools)} tool call(s) in reasoning_content (fallback)!", flush=True)
                        if reasoning_text.strip() and not text_block_closed:
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': reasoning_text}})}\n\n"
                        if not text_block_closed:
                            text_block_closed = True
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                        for tc in reasoning_tools:
                            last_tool_index += 1
                            print(f"[streaming] XML tool_use from reasoning (fallback): name={tc['name']} index={last_tool_index}", flush=True)
                            for event in _emit_tool_use_block(tc["name"], tc["input"], last_tool_index):
                                yield event
                    else:
                        if not text_block_closed:
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': reasoning_buffer}})}\n\n"
                else:
                    if not text_block_closed:
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': reasoning_buffer}})}\n\n"
            elif reasoning_buffer and (has_xml_tool_calls or tool_index is not None):
                print(f"[streaming] Suppressed {len(reasoning_buffer)} chars of reasoning_content in fallback close (tool calls present)")

            if not text_block_closed:
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

            fallback_stop = "tool_use" if (has_xml_tool_calls or tool_index is not None) else "end_turn"
            print(f"[streaming] FALLBACK CLOSE: stop_reason={fallback_stop} tool_index={tool_index} has_xml={has_xml_tool_calls} no_tools={no_tools_mode}", flush=True)
            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': fallback_stop, 'stop_sequence': None}, 'usage': {'output_tokens': scale_tokens(output_tokens, model_context_window)}})}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
            yield "data: [DONE]\n\n"

    except Exception as e:
        print(f"[streaming] FATAL stream error: {type(e).__name__}: {e}", flush=True)
        yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 0}})}\n\n"
        yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
        yield "data: [DONE]\n\n"
