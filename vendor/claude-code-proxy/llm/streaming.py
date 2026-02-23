# llm/streaming.py
"""Convert LiteLLM (OpenAI-style) streaming chunks to Anthropic SSE events.

The public entry point is ``handle_streaming()``.  Internally, all mutable
state lives in a ``_StreamCtx`` dataclass and every repeated pattern is
factored into a small helper that returns ``list[str]`` of SSE events.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from json_repair import repair_json

from utils.utils import bget, make_tool_id, map_stop_reason, scale_tokens, TOOL_ID_PREFIX
import llm.sse as sse
from llm.tool_prompting import (
    is_no_tools_model,
    XmlToolBuffer,
    recover_incomplete_tool_call,
    extract_tool_calls_from_text,
    strip_tool_call_xml,
    _build_valid_tool_names,
    validate_tool_name,
)


# ── JSON repair helpers (unchanged — already clean) ──────────────────

def _close_json_brackets(text: str) -> str:
    """Compute minimal suffix to close all open brackets/braces/strings in JSON text."""
    in_string = False
    escape_next = False
    stack: list[str] = []

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
    """Detect if repaired JSON has truncation artifacts (all-empty string values)."""
    try:
        parsed = json.loads(json_str)
    except Exception:
        return True

    def _check(obj: Any) -> bool:
        if isinstance(obj, dict):
            string_vals = [v for v in obj.values() if isinstance(v, str)]
            if len(string_vals) >= 2 and all(v == '' for v in string_vals):
                return True
            return any(_check(v) for v in obj.values() if isinstance(v, (dict, list)))
        if isinstance(obj, list):
            return any(_check(item) for item in obj if isinstance(item, (dict, list)))
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

    # Strategy 1: json_repair library (only safe if it only appends)
    try:
        repaired_str = repair_json(accumulated)
        if repaired_str.startswith(accumulated):
            suffix = repaired_str[len(accumulated):]
            if suffix:
                json.loads(accumulated + suffix)
                print(f"[json-repair] Streaming: library suffix for tool index {tool_index} ({len(suffix)} chars)")
                return suffix
    except Exception:
        pass

    # Strategy 2: Manual bracket/brace closer (always safe)
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


# ── Quality warnings ─────────────────────────────────────────────────

def _warn_empty_tool_values(name: str, input_dict: dict) -> None:
    """Log a warning if tool arguments contain suspiciously empty values."""
    if name == "TodoWrite":
        todos = input_dict.get("todos", [])
        if todos and isinstance(todos, list):
            empty_content = sum(1 for t in todos if isinstance(t, dict) and not t.get("content"))
            empty_active = sum(1 for t in todos if isinstance(t, dict) and not t.get("activeForm"))
            missing_status = sum(1 for t in todos if isinstance(t, dict) and t.get("status") not in ("pending", "in_progress", "completed"))
            if empty_content or empty_active or missing_status:
                print(f"[quality] WARNING: TodoWrite {len(todos)} items — empty content={empty_content}, empty activeForm={empty_active}, bad status={missing_status}")
    elif name == "Write":
        if input_dict.get("content", None) == "":
            print(f"[quality] WARNING: Write tool has empty 'content' — model likely truncated")
    elif name == "Edit":
        if input_dict.get("old_string", None) == "" and input_dict.get("new_string", None) == "":
            print(f"[quality] WARNING: Edit tool has empty old_string and new_string")


# ── Streaming context ────────────────────────────────────────────────

@dataclass
class _StreamCtx:
    """Mutable state tracked across streaming chunks.

    Groups all flags, counters, and buffers that handle_streaming()
    maintains between SSE events so helpers can read/write them.
    """
    # XML tool simulation
    no_tools_mode: bool
    request_tools: Any
    valid_names: set[str]
    xml_tool_buffer: XmlToolBuffer | None

    # Content tracking
    accumulated_text: str = ""
    text_sent: bool = False
    text_block_closed: bool = False

    # Native tool call tracking
    tool_index: int | None = None          # current provider-side tool index
    last_tool_index: int = 0               # our sequential index across all tools
    tool_args_buffer: dict[int, str] = field(default_factory=dict)

    # XML tool tracking
    has_xml_tool_calls: bool = False

    # Reasoning buffer (deepseek-reasoner)
    reasoning_buffer: str = ""

    # Token accounting
    output_tokens: int = 0

    # Protocol state
    has_sent_stop_reason: bool = False

    @property
    def has_any_tools(self) -> bool:
        return self.has_xml_tool_calls or self.tool_index is not None


# ── Extracted helpers (each returns list[str] of SSE events) ─────────

def _emit_tool_use_block(name: str, input_dict: dict, block_index: int) -> list[str]:
    """Generate SSE events for a single tool_use block (Anthropic spec)."""
    _warn_empty_tool_values(name, input_dict)
    tool_id = make_tool_id()
    args_json = json.dumps(input_dict, ensure_ascii=False)
    return [
        sse.content_block_start_tool(block_index, tool_id, name),
        # Initial empty partial_json — required by Anthropic SSE protocol.
        # CC uses this as initialization signal for its JSON accumulator.
        sse.input_json_delta(block_index, ""),
        sse.input_json_delta(block_index, args_json),
        sse.content_block_stop(block_index),
    ]


def _close_text_block(ctx: _StreamCtx) -> list[str]:
    """Close the text content block (index 0) if still open.

    Flushes any unsent accumulated text first.
    """
    if ctx.text_block_closed:
        return []
    events: list[str] = []
    if ctx.accumulated_text and not ctx.text_sent:
        ctx.text_sent = True
        events.append(sse.text_delta(0, ctx.accumulated_text))
    ctx.text_block_closed = True
    events.append(sse.content_block_stop(0))
    return events


def _emit_text_segment(ctx: _StreamCtx, text: str) -> list[str]:
    """Emit a text segment as a text_delta if the text block is still open."""
    if not text.strip() or ctx.text_block_closed:
        return []
    ctx.accumulated_text += text
    ctx.text_sent = True
    return [sse.text_delta(0, text)]


def _emit_xml_tool(ctx: _StreamCtx, name: str, input_dict: dict) -> list[str]:
    """Close text block and emit a tool_use block from XML parsing."""
    ctx.has_xml_tool_calls = True
    events = _close_text_block(ctx)
    ctx.last_tool_index += 1
    print(f"[streaming] XML tool_use emitted: name={name} index={ctx.last_tool_index}", flush=True)
    events.extend(_emit_tool_use_block(name, input_dict, ctx.last_tool_index))
    return events


async def _flush_xml_buffer(ctx: _StreamCtx) -> list[str]:
    """Flush the XML tool buffer and process all remaining segments.

    Handles three segment types from XmlToolBuffer.flush():
    - "incomplete_tool_call" → 3-level recovery (deterministic → LLM → strip)
    - "text" → emit as text_delta
    - "tool_call" → close text, emit tool_use block
    """
    if not ctx.xml_tool_buffer:
        return []
    events: list[str] = []
    for segment in ctx.xml_tool_buffer.flush():
        seg_type = segment["type"]
        if seg_type == "incomplete_tool_call":
            events.extend(await _recover_incomplete_tool(ctx, segment["text"]))
        elif seg_type == "text":
            # Strip orphaned inner tags that may appear without <tool_call> wrapper
            clean_text = re.sub(r'</?(?:arg_key|arg_value)>', '', segment["text"])
            events.extend(_emit_text_segment(ctx, clean_text))
        elif seg_type == "tool_call":
            events.extend(_emit_xml_tool(ctx, segment["name"], segment["input"]))
    return events


async def _recover_incomplete_tool(ctx: _StreamCtx, partial_xml: str) -> list[str]:
    """3-level recovery for incomplete <tool_call> XML (both formats).

    Runs BEFORE message_stop — SSE stream is still open.
    Level 1: Deterministic (json_repair / argkv extraction) — instant
    Level 2: LLM re-completion (CLASSIFIER_MODEL) — 3s timeout
    Level 3: Clean fallback (strip XML, emit text or suppress)
    """
    events: list[str] = []

    # Levels 1+2: reuse existing recover_incomplete_tool_call()
    recovered = await recover_incomplete_tool_call(
        partial_xml=partial_xml,
        tools=ctx.request_tools,
        model=os.environ.get("CLASSIFIER_MODEL", "openai/deepseek-chat"),
        api_key=os.environ.get("CLASSIFIER_API_KEY", ""),
        api_base=os.environ.get("CLASSIFIER_BASE_URL"),
    )
    if recovered:
        if ctx.valid_names:
            recovered = [tc for tc in recovered
                         if validate_tool_name(tc.get("name", ""), ctx.valid_names)]
        if recovered:
            print(f"[recovery] OK: {len(recovered)} tool(s) recovered from incomplete XML")
            for tc in recovered:
                events.extend(_emit_xml_tool(ctx, tc["name"], tc["input"]))
            return events

    # Level 3: strip XML, emit clean text (never raw XML to CC)
    clean = strip_tool_call_xml(partial_xml)
    if clean:
        print(f"[recovery] FALLBACK: emitting {len(clean)} chars clean text "
              f"(stripped from {len(partial_xml)} chars)")
        events.extend(_emit_text_segment(ctx, clean))
    else:
        print(f"[recovery] FALLBACK: suppressed {len(partial_xml)} chars "
              f"(no text after XML stripping)")
    return events


def _close_native_tool_blocks(ctx: _StreamCtx, finish_reason: str | None) -> tuple[list[str], int]:
    """Repair JSON and close all native tool_call content blocks.

    Returns (events, valid_count) where valid_count is how many blocks
    have valid JSON (used to decide stop_reason).
    """
    if ctx.tool_index is None:
        return [], 0
    events: list[str] = []
    valid_count = 0
    for i in range(1, ctx.last_tool_index + 1):
        accumulated = ctx.tool_args_buffer.get(i, "")
        is_valid = False
        was_repaired = False

        suffix = _compute_repair_suffix(accumulated, i)
        if suffix:
            events.append(sse.input_json_delta(i, suffix))
            accumulated += suffix
            was_repaired = True

        if accumulated:
            try:
                json.loads(accumulated)
                if was_repaired and finish_reason == "length" and _has_truncation_artifacts(accumulated):
                    print(f"[streaming] WARNING: tool index {i} repair produced truncation artifacts, marking invalid")
                else:
                    is_valid = True
            except json.JSONDecodeError:
                print(f"[streaming] WARNING: tool index {i} has irrecoverable JSON ({len(accumulated)} chars)")
        else:
            is_valid = True  # empty args = valid {}

        events.append(sse.content_block_stop(i))
        if is_valid:
            valid_count += 1
    return events, valid_count


def _process_reasoning_buffer(ctx: _StreamCtx, label: str = "") -> list[str]:
    """Handle buffered reasoning_content at stream end.

    If no tool calls are present, emits reasoning as text.
    If reasoning contains <tool_call> XML, extracts and emits tools.
    If tool calls ARE present, suppresses reasoning (crashes CC).
    """
    if not ctx.reasoning_buffer:
        return []
    if ctx.has_any_tools:
        print(f"[streaming] Suppressed {len(ctx.reasoning_buffer)} chars of reasoning_content{label} (tool calls present)")
        return []

    events: list[str] = []

    # deepseek-reasoner sometimes puts <tool_call> XML in reasoning_content
    if "<tool_call" in ctx.reasoning_buffer:
        reasoning_tools, reasoning_text = extract_tool_calls_from_text(
            ctx.reasoning_buffer, valid_tool_names=ctx.valid_names, tools=ctx.request_tools,
        )
        if reasoning_tools:
            print(f"[streaming] Found {len(reasoning_tools)} tool call(s) in reasoning_content{label}!", flush=True)
            if reasoning_text.strip():
                events.extend(_emit_text_segment(ctx, reasoning_text))
            events.extend(_close_text_block(ctx))
            for tc in reasoning_tools:
                ctx.has_xml_tool_calls = True
                ctx.last_tool_index += 1
                print(f"[streaming] XML tool_use from reasoning{label}: name={tc['name']} index={ctx.last_tool_index}", flush=True)
                events.extend(_emit_tool_use_block(tc["name"], tc["input"], ctx.last_tool_index))
            return events
        # <tool_call> found but regex didn't match — fall through to emit as text

    if not ctx.text_block_closed:
        ctx.text_sent = True
        events.append(sse.text_delta(0, ctx.reasoning_buffer))
    return events


def _compute_stream_stop_reason(
    ctx: _StreamCtx, finish_reason: str | None, valid_tool_blocks: int,
) -> str:
    """Determine the Anthropic stop_reason from stream state.

    Special handling for finish_reason=length: only report "tool_use"
    if all native tool blocks have valid JSON.
    """
    total_native = ctx.last_tool_index if ctx.tool_index is not None else 0
    if finish_reason == "length":
        if ctx.has_any_tools and valid_tool_blocks == total_native:
            return "tool_use"
        return "max_tokens"
    return map_stop_reason(finish_reason, ctx.has_any_tools)


def _emit_stream_end(ctx: _StreamCtx, stop_reason: str, model_context_window: int) -> list[str]:
    """Emit the final message_delta, message_stop, and [DONE] events."""
    return [
        sse.message_delta(stop_reason, scale_tokens(ctx.output_tokens, model_context_window)),
        sse.message_stop(),
        sse.done(),
    ]


# ── Main streaming handler ───────────────────────────────────────────

async def handle_streaming(response_generator: Any, original_request: Any, model_context_window: int = 0):
    """Convert a LiteLLM streaming response to Anthropic SSE events."""
    try:
        model = getattr(original_request, "original_model", None) or original_request.model
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"

        # Initialize streaming context
        no_tools_mode = is_no_tools_model(original_request.model)
        request_tools = getattr(original_request, "tools", None)
        valid_names = _build_valid_tool_names(request_tools) if request_tools else set()
        ctx = _StreamCtx(
            no_tools_mode=no_tools_mode,
            request_tools=request_tools,
            valid_names=valid_names,
            xml_tool_buffer=XmlToolBuffer(valid_tool_names=valid_names, tools=request_tools) if request_tools else None,
        )
        print(f"[streaming] INIT: model={original_request.model} no_tools_mode={no_tools_mode} xml_buffer={'YES' if ctx.xml_tool_buffer else 'NO'}", flush=True)

        # Open the stream
        yield sse.message_start(msg_id, model)
        yield sse.content_block_start_text(0)
        yield sse.ping()

        # ── Process chunks ───────────────────────────────────────────
        async for chunk in response_generator:
            try:
                # Track usage
                if hasattr(chunk, "usage") and chunk.usage is not None:
                    if hasattr(chunk.usage, "completion_tokens"):
                        ctx.output_tokens = chunk.usage.completion_tokens

                if not hasattr(chunk, "choices") or not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None) or getattr(choice, "message", None) or {}
                finish_reason = getattr(choice, "finish_reason", None)

                # ── Reasoning content (deepseek-reasoner, GLM-4.7) ──
                delta_reasoning = bget(delta, "reasoning_content")
                if delta_reasoning:
                    if "<tool_call" in delta_reasoning:
                        print(f"[streaming] DIAG: <tool_call in reasoning_content! "
                              f"({len(delta_reasoning)} chars) no_tools={no_tools_mode}", flush=True)
                    if no_tools_mode:
                        ctx.reasoning_buffer += delta_reasoning
                    elif ctx.xml_tool_buffer:
                        # GLM-4.7 may put <tool_call> XML in reasoning_content.
                        # Feed through buffer to detect and extract tool calls.
                        for segment in ctx.xml_tool_buffer.feed(delta_reasoning):
                            if segment["type"] == "text":
                                ctx.accumulated_text += segment["text"]
                                if ctx.tool_index is None and not ctx.text_block_closed:
                                    ctx.text_sent = True
                                    yield sse.text_delta(0, segment["text"])
                            elif segment["type"] == "tool_call":
                                for ev in _emit_xml_tool(ctx, segment["name"], segment["input"]):
                                    yield ev
                    elif ctx.tool_index is None and not ctx.text_block_closed:
                        ctx.text_sent = True
                        yield sse.text_delta(0, delta_reasoning)

                # ── Text content ─────────────────────────────────────
                delta_content = bget(delta, "content")
                if delta_content:
                    if "<tool_call" in delta_content:
                        print(f"[streaming] DIAG: <tool_call in delta.content! "
                              f"({len(delta_content)} chars) buffer={'YES' if ctx.xml_tool_buffer else 'NO'} "
                              f"text_closed={ctx.text_block_closed} first200={delta_content[:200]}", flush=True)
                    if ctx.xml_tool_buffer:
                        for segment in ctx.xml_tool_buffer.feed(delta_content):
                            if segment["type"] == "text":
                                ctx.accumulated_text += segment["text"]
                                if ctx.tool_index is None and not ctx.text_block_closed:
                                    ctx.text_sent = True
                                    yield sse.text_delta(0, segment["text"])
                            elif segment["type"] == "tool_call":
                                for ev in _emit_xml_tool(ctx, segment["name"], segment["input"]):
                                    yield ev
                    else:
                        ctx.accumulated_text += delta_content
                        if ctx.tool_index is None and not ctx.text_block_closed:
                            ctx.text_sent = True
                            yield sse.text_delta(0, delta_content)

                # ── Native tool calls ────────────────────────────────
                delta_tool_calls = bget(delta, "tool_calls")
                if delta_tool_calls:
                    if ctx.tool_index is None:
                        for ev in _close_text_block(ctx):
                            yield ev

                    if not isinstance(delta_tool_calls, list):
                        delta_tool_calls = [delta_tool_calls]

                    for tool_call in delta_tool_calls:
                        # Determine provider-side tool index
                        if isinstance(tool_call, dict) and "index" in tool_call:
                            current_index = tool_call["index"]
                        elif hasattr(tool_call, "index"):
                            current_index = tool_call.index
                        else:
                            current_index = 0

                        # New tool block?
                        if ctx.tool_index is None or current_index != ctx.tool_index:
                            ctx.tool_index = current_index
                            ctx.last_tool_index += 1

                            if isinstance(tool_call, dict):
                                function = tool_call.get("function", {}) or {}
                                name = function.get("name", "")
                                raw_id = tool_call.get("id", "")
                            else:
                                function = getattr(tool_call, "function", None)
                                name = getattr(function, "name", "") if function else ""
                                raw_id = getattr(tool_call, "id", "") or ""

                            tool_id = raw_id if raw_id.startswith(TOOL_ID_PREFIX) else make_tool_id()
                            if not name:
                                print(f"[streaming] WARNING: Skipping tool_call with empty name (index={current_index})")
                                continue

                            yield sse.content_block_start_tool(ctx.last_tool_index, tool_id, name)
                            yield sse.input_json_delta(ctx.last_tool_index, "")

                        # Arguments delta
                        if isinstance(tool_call, dict):
                            function = tool_call.get("function", {}) or {}
                            arguments = function.get("arguments", "")
                        else:
                            function = getattr(tool_call, "function", None)
                            arguments = getattr(function, "arguments", "") if function else ""

                        if arguments:
                            if ctx.last_tool_index not in ctx.tool_args_buffer:
                                ctx.tool_args_buffer[ctx.last_tool_index] = ""
                            ctx.tool_args_buffer[ctx.last_tool_index] += arguments
                            yield sse.input_json_delta(ctx.last_tool_index, arguments)

                # ── Finish ───────────────────────────────────────────
                if finish_reason and not ctx.has_sent_stop_reason:
                    ctx.has_sent_stop_reason = True

                    for ev in await _flush_xml_buffer(ctx):
                        yield ev

                    # Safety net: catch <tool_call> XML in accumulated text that
                    # the buffer missed (e.g. arrived via reasoning_content before
                    # the buffer fix, or any other bypass path).
                    if not ctx.has_xml_tool_calls and "<tool_call" in ctx.accumulated_text:
                        safety_tools, _ = extract_tool_calls_from_text(
                            ctx.accumulated_text, valid_tool_names=ctx.valid_names, tools=ctx.request_tools,
                        )
                        if safety_tools:
                            print(f"[streaming] SAFETY NET: {len(safety_tools)} tool(s) in accumulated text "
                                  f"that buffer missed!", flush=True)
                            for tc in safety_tools:
                                for ev in _emit_xml_tool(ctx, tc["name"], tc["input"]):
                                    yield ev

                    tool_events, valid_tool_blocks = _close_native_tool_blocks(ctx, finish_reason)
                    for ev in tool_events:
                        yield ev
                    for ev in _process_reasoning_buffer(ctx):
                        yield ev
                    for ev in _close_text_block(ctx):
                        yield ev

                    stop_reason = _compute_stream_stop_reason(ctx, finish_reason, valid_tool_blocks)
                    print(f"[streaming] CLOSE: stop_reason={stop_reason} finish_reason={finish_reason} tool_index={ctx.tool_index} has_xml={ctx.has_xml_tool_calls} no_tools={no_tools_mode}", flush=True)
                    for ev in _emit_stream_end(ctx, stop_reason, model_context_window):
                        yield ev
                    return

            except Exception as e:
                print(f"[streaming] chunk error (skipped): {type(e).__name__}: {e}")
                continue

        # ── Fallback close (stream ended without finish_reason) ──────
        if not ctx.has_sent_stop_reason:
            for ev in await _flush_xml_buffer(ctx):
                yield ev

            # Safety net (fallback path)
            if not ctx.has_xml_tool_calls and "<tool_call" in ctx.accumulated_text:
                safety_tools, _ = extract_tool_calls_from_text(
                    ctx.accumulated_text, valid_tool_names=ctx.valid_names, tools=ctx.request_tools,
                )
                if safety_tools:
                    print(f"[streaming] SAFETY NET (fallback): {len(safety_tools)} tool(s) in accumulated text!", flush=True)
                    for tc in safety_tools:
                        for ev in _emit_xml_tool(ctx, tc["name"], tc["input"]):
                            yield ev

            if ctx.tool_index is not None:
                for i in range(1, ctx.last_tool_index + 1):
                    suffix = _compute_repair_suffix(ctx.tool_args_buffer.get(i, ""), i)
                    if suffix:
                        yield sse.input_json_delta(i, suffix)
                    yield sse.content_block_stop(i)

            for ev in _process_reasoning_buffer(ctx, label=" (fallback)"):
                yield ev
            for ev in _close_text_block(ctx):
                yield ev

            fallback_stop = "tool_use" if ctx.has_any_tools else "end_turn"
            print(f"[streaming] FALLBACK CLOSE: stop_reason={fallback_stop} tool_index={ctx.tool_index} has_xml={ctx.has_xml_tool_calls} no_tools={no_tools_mode}", flush=True)
            for ev in _emit_stream_end(ctx, fallback_stop, model_context_window):
                yield ev

    except Exception as e:
        print(f"[streaming] FATAL stream error: {type(e).__name__}: {e}", flush=True)
        yield sse.message_delta("end_turn", 0)
        yield sse.message_stop()
        yield sse.done()
