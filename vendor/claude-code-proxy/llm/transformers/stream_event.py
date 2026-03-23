"""
Stream Event Transformer

AGNOSTIC transformer for handling streaming SSE events across ALL models.

Consolidates ALL streaming event processing logic from streaming.py:
- Reasoning tag stripping (_ReasoningStripper, _strip_think_tags)
- JSON repair helpers (_close_json_brackets, _compute_repair_suffix, etc.)
- Streaming context (_StreamCtx)
- SSE event helpers (_emit_tool_use_block, _close_text_block, etc.)
- Main streaming handler (handle_streaming)
- Passthrough XML tool extraction (passthrough_xml_tool_extraction)

CRITICAL DESIGN REQUIREMENT: AGNOSTIC (NO MODEL-SPECIFIC LOGIC)
- Zero checks of model_name, model patterns, or provider quirks
- Same behavior for ALL models
- Future-proof: New models automatically supported

streaming.py is now a thin re-export shim — all logic lives here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from json_repair import repair_json

from utils.utils import bget, make_tool_id, map_stop_reason, scale_tokens, TOOL_ID_PREFIX
from utils.metrics import metrics
from utils.quality import score_response as score_response_quality
from utils.tool_utils import (
    is_no_tools_model,
    build_valid_tool_names as _build_valid_tool_names,
    validate_tool_name,
)
import llm.sse as sse
from llm.pipeline import Transformer, TransformContext
from llm.transformers.universal_tool_extraction import (
    XmlToolBuffer,
    recover_incomplete_tool_call,
    extract_tool_calls_from_text,
    strip_tool_call_xml,
)

# Import these via TYPE_CHECKING to avoid circular dependency
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from llm.transformers.quality_refinement import _validate_intent_outcome

logger = logging.getLogger(__name__)


# ── Post-Stream Grounding Validation ───────────────────────────────────────

async def _run_post_stream_validation(
    accumulated_text: str,
    tool_names: list[str],
    request: Any,
    ctx: TransformContext,
    cfg: Any,
    session_id: str | None = None,
) -> None:
    """Run grounding validation on accumulated streaming response after completion.

    This function is called AFTER the streaming response completes to validate
    grounding for streaming requests. The validation runs asynchronously and
    doesn't block the client response.

    Args:
        accumulated_text: Full accumulated text from the streaming response
        tool_names: List of tool names used in the response
        request: Original request object (for messages access)
        ctx: TransformContext with intent/analysis state
        cfg: ProxyConfig with grounding settings
        session_id: Optional session ID for multi-hop tracking
    """
    # Import here to avoid circular dependency
    from llm.transformers.grounding_validator import GroundingValidatorTransformer
    from llm.compressor import _track_grounding_hop

    # Skip if grounding validation is disabled or not in analysis mode
    if not ctx.is_analysis:
        return
    if not getattr(cfg, "policy", None):
        return
    if not cfg.policy.grounding_validation_enabled:
        return

    # Skip if no text to validate
    if not accumulated_text.strip():
        logger.debug("[post-stream-grounding] No text to validate (text_len=%d)", len(accumulated_text))
        return

    # Skip if response was tool-only (no explanatory text)
    # This is normal for PLAN mode where model outputs tool_use directly
    if len(accumulated_text.strip()) < 100 and not tool_names:
        logger.debug("[post-stream-grounding] Tool-only response, skipping grounding validation")
        return

    logger.info(
        "[post-stream-grounding] Start: text_len=%d tools=%d is_analysis=%s grounding=%s multihop=%s",
        len(accumulated_text), len(tool_names), ctx.is_analysis,
        getattr(cfg, "policy", {}).get("grounding_validation_enabled", False) if hasattr(cfg, "policy") else False,
        getattr(cfg, "policy", {}).get("multihop_grounding_enabled", False) if hasattr(cfg, "policy") else False
    )

    # Create a mock response object for GroundingValidator
    from types import SimpleNamespace
    mock_response = SimpleNamespace(
        content=[{"type": "text", "text": accumulated_text}],
        messages=getattr(request, "messages", []),
    )

    # Run grounding validation
    grounding_validator = GroundingValidatorTransformer(enabled=True)

    # Create a new context for validation (reuse key fields from ctx)
    validation_ctx = TransformContext(
        intent=ctx.intent,
        is_analysis=ctx.is_analysis,
        phase=ctx.phase,
        analysis_phase=ctx.analysis_phase,
        session_id=session_id,
    )

    await grounding_validator.transform(mock_response, validation_ctx)

    # Log grounding results
    logger.info(
        "[post-stream-grounding] Complete: score=%.0f%% citations=%d issues=%d multihop=%s",
        validation_ctx.grounding_score * 100,
        len(validation_ctx.evidence_links),
        len(validation_ctx.grounding_issues),
        "YES" if (session_id and hasattr(cfg, "policy") and cfg.policy.multihop_grounding_enabled) else "NO"
    )

    # Track multi-hop relationships if enabled
    if session_id and cfg.policy.multihop_grounding_enabled if hasattr(cfg, "policy") and hasattr(cfg.policy, "multihop_grounding_enabled") else False:

        # Track grounding hops for multi-hop validation
        # This is async but runs in background, doesn't block the response
        for citation, evidence in validation_ctx.evidence_links.items():
            file_path = evidence[0] if evidence else ""
            if file_path:
                # Extract entity from citation
                entity_a = citation.split(":")[0].split("/")[-1].split(".")[0] if citation else ""
                if entity_a and file_path:
                    await _track_grounding_hop(
                        session_id=session_id,
                        entity_a=entity_a,
                        entity_b=f"{entity_a}_verified",
                        evidence=[citation],
                        code_snippet=evidence[1] if len(evidence) > 1 else "",
                    )


# ── Constants ───────────────────────────────────────────────────────────────

# Known Claude Code workflow tools injected via <available-deferred-tools> in system prompt.
# These must never be filtered as "hallucinated" — models may legitimately call them even
# when the current request's system prompt omits the deferred-tools block (e.g. BUILD turns
# that follow a PLAN turn where the model already saw ExitPlanMode in conversation history).
_CC_WORKFLOW_TOOLS: frozenset[str] = frozenset({
    "EnterPlanMode", "ExitPlanMode", "TodoWrite",
    "AskUserQuestion", "CronCreate", "CronDelete", "CronList",
    "EnterWorktree", "ExitWorktree", "TaskOutput", "TaskStop",
    "NotebookEdit", "WebFetch", "WebSearch",
})


# ── Event type constants (AGNOSTIC - same for ALL models) ────────────

EVENT_CONTENT_BLOCK_START = "content_block_start"
EVENT_CONTENT_BLOCK_DELTA = "content_block_delta"
EVENT_CONTENT_BLOCK_STOP = "content_block_stop"
EVENT_TEXT_DELTA = "text_delta"
EVENT_MESSAGE_DELTA = "message_delta"
EVENT_MESSAGE_STOP = "message_stop"
EVENT_ERROR = "error"


# ── Reasoning tag stripping ──────────────────────────────────────────

# Pre-compiled for performance (called per streaming chunk)
_THINK_TAG_RE = re.compile(r'</?think>')


def _strip_think_tags(text: str) -> str:
    """Strip <think></think> reasoning tags that some models emit (Qwen, GLM)."""
    if "<think>" in text or "</think>" in text:
        return _THINK_TAG_RE.sub('', text)
    return text


class _ReasoningStripper:
    """Stateful stripper for <reasoning>...</reasoning> tags in streaming text.

    Handles tags split across multiple chunks. When STRIP_REASONING=1,
    all content between <reasoning> and </reasoning> is suppressed.
    """

    def __init__(self):
        self._inside = False
        self._buffer = ""

    def process(self, text: str) -> str:
        """Process a text chunk, stripping reasoning content.

        Returns the text with reasoning tags and their content removed.
        """
        if not text:
            return text

        result = []
        self._buffer += text

        while self._buffer:
            if not self._inside:
                # Look for opening <reasoning> tag
                idx = self._buffer.find("<reasoning>")
                if idx == -1:
                    # No opening tag — check for partial tag at end
                    safe_end = len(self._buffer)
                    for i in range(1, min(len("<reasoning>"), len(self._buffer)) + 1):
                        if "<reasoning>".startswith(self._buffer[-i:]):
                            safe_end = len(self._buffer) - i
                            break
                    if safe_end > 0:
                        result.append(self._buffer[:safe_end])
                        self._buffer = self._buffer[safe_end:]
                    break
                else:
                    # Emit text before the tag
                    if idx > 0:
                        result.append(self._buffer[:idx])
                    self._buffer = self._buffer[idx + len("<reasoning>"):]
                    self._inside = True
            else:
                # Inside reasoning — look for closing tag
                idx = self._buffer.find("</reasoning>")
                if idx == -1:
                    # No closing tag yet — discard buffered reasoning content
                    self._buffer = ""
                    break
                else:
                    # Skip everything up to and including the closing tag
                    self._buffer = self._buffer[idx + len("</reasoning>"):]
                    self._inside = False

        return "".join(result)


# ── JSON repair helpers ──────────────────────────────────────────────

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


_DANGEROUS_EMPTY_KEYS = frozenset({
    "file_path", "content", "old_string", "new_string", "command",
    "notebook_path", "new_source", "pattern", "query", "url",
})


def _has_truncation_artifacts(json_str: str) -> bool:
    """Detect if repaired JSON has truncation artifacts.

    Checks for:
    1. All-empty string values in dicts with 2+ strings (original check)
    2. Empty values for known-dangerous tool parameters (file_path, content, etc.)
    """
    try:
        parsed = json.loads(json_str)
    except Exception:
        return True

    def _check(obj: Any) -> bool:
        if isinstance(obj, dict):
            string_vals = [v for v in obj.values() if isinstance(v, str)]
            if len(string_vals) >= 2 and all(v == '' for v in string_vals):
                return True
            # Check for empty dangerous parameters (e.g. Write with empty file_path)
            for key in _DANGEROUS_EMPTY_KEYS:
                if key in obj and obj[key] == '':
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
    hallucinated_tool_indices: set[int] = field(default_factory=set)

    # XML tool tracking
    has_xml_tool_calls: bool = False

    # Reasoning buffer (deepseek-reasoner)
    reasoning_buffer: str = ""

    # Token accounting
    output_tokens: int = 0
    thinking_chars: int = 0  # chars in reasoning_content (for cost estimation)

    # Protocol state
    has_sent_stop_reason: bool = False

    # Model identification (for per-model metrics)
    model_id: str = ""

    # Classifier config (for tool recovery LLM calls)
    classifier_model: str = ""
    classifier_api_key: str = ""
    classifier_base_url: str | None = None

    # Reasoning tag stripping (STRIP_REASONING=1)
    reasoning_stripper: _ReasoningStripper | None = None

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
    metrics.increment_tool_counter("xml_extracted")
    if ctx.model_id:
        metrics.record_model_event(ctx.model_id, "tool_success")
    events = _close_text_block(ctx)
    ctx.last_tool_index += 1
    print(f"[streaming] XML tool_use emitted: name={name} index={ctx.last_tool_index}", flush=True)
    events.extend(_emit_tool_use_block(name, input_dict, ctx.last_tool_index))
    return events


def _process_buffer_segments(
    ctx: _StreamCtx,
    chunk: str,
    emit_text: bool,
) -> list[str]:
    """Feed a chunk through XmlToolBuffer and process resulting segments.

    Used for both reasoning_content and content streams.
    - emit_text=True: text goes to accumulated_text + text_delta SSE (content, GLM reasoning)
    - emit_text=False: text goes to reasoning_buffer only (DeepSeek reasoning)
    Tool call segments always emit via _emit_xml_tool.
    """
    if not ctx.xml_tool_buffer:
        return []
    events: list[str] = []
    for segment in ctx.xml_tool_buffer.feed(chunk):
        if segment["type"] == "text":
            text = segment["text"]
            # Never leak raw <tool_call> XML as text to Claude Code
            if "<tool_call" in text:
                text = strip_tool_call_xml(text)
            text = _strip_think_tags(text)
            if not text:
                continue
            if emit_text:
                ctx.accumulated_text += text
                if ctx.tool_index is None and not ctx.text_block_closed:
                    ctx.text_sent = True
                    events.append(sse.text_delta(0, text))
            else:
                ctx.reasoning_buffer += text
        elif segment["type"] == "tool_call":
            events.extend(_emit_xml_tool(ctx, segment["name"], segment["input"]))
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
        model=ctx.classifier_model or "openai/deepseek-chat",
        api_key=ctx.classifier_api_key,
        api_base=ctx.classifier_base_url,
    )
    if recovered:
        if ctx.valid_names:
            recovered = [tc for tc in recovered
                         if validate_tool_name(tc.get("name", ""), ctx.valid_names)]
        if recovered:
            metrics.increment_tool_counter("recovered")
            print(f"[recovery] OK: {len(recovered)} tool(s) recovered from incomplete XML")
            for tc in recovered:
                events.extend(_emit_xml_tool(ctx, tc["name"], tc["input"]))
            return events

    # Level 3: strip XML, emit clean text (never raw XML to CC)
    metrics.increment_tool_counter("truncated")
    if ctx.model_id:
        metrics.record_model_event(ctx.model_id, "tool_failure")
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


def _estimate_output_tokens(ctx: _StreamCtx) -> int:
    """Estimate output tokens from all accumulated content when provider didn't report.

    Counts text + native tool arguments + reasoning buffer (chars/3 heuristic).
    Does NOT include thinking_chars (those are tracked separately for cost).
    """
    total_chars = len(ctx.accumulated_text)
    for args in ctx.tool_args_buffer.values():
        total_chars += len(args)
    total_chars += len(ctx.reasoning_buffer)
    return max(1, total_chars // 3) if total_chars > 0 else 0


# ── Main streaming handler ───────────────────────────────────────────

async def handle_streaming(
    response_generator: Any,
    original_request: Any,
    model_context_window: int = 0,
    classifier_model: str = "",
    classifier_api_key: str = "",
    classifier_base_url: str | None = None,
    strip_reasoning: bool = False,
):
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
            model_id=original_request.model,
            classifier_model=classifier_model,
            classifier_api_key=classifier_api_key,
            classifier_base_url=classifier_base_url,
            reasoning_stripper=_ReasoningStripper() if strip_reasoning else None,
        )
        print(f"[streaming] INIT: model={original_request.model} no_tools_mode={no_tools_mode} "
              f"xml_buffer={'YES' if ctx.xml_tool_buffer else 'NO'} strip_reasoning={strip_reasoning}", flush=True)

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
                    ctx.thinking_chars += len(delta_reasoning)
                    if "<tool_call" in delta_reasoning:
                        print(f"[streaming] DIAG: <tool_call in reasoning_content! "
                              f"({len(delta_reasoning)} chars) no_tools={no_tools_mode}", flush=True)
                    if no_tools_mode:
                        # DeepSeek-reasoner: emit_text=False → reasoning_buffer
                        # (reasoning is 5-15K tokens, crashes CC's SSE parser)
                        if ctx.xml_tool_buffer:
                            for ev in _process_buffer_segments(ctx, delta_reasoning, emit_text=False):
                                yield ev
                        else:
                            delta_reasoning = _strip_think_tags(delta_reasoning)
                            if delta_reasoning:
                                ctx.reasoning_buffer += delta_reasoning
                    elif ctx.xml_tool_buffer:
                        # GLM-4.7: emit_text=True → accumulated_text + text_delta
                        for ev in _process_buffer_segments(ctx, delta_reasoning, emit_text=True):
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
                        for ev in _process_buffer_segments(ctx, delta_content, emit_text=True):
                            yield ev
                    else:
                        delta_content = _strip_think_tags(delta_content)
                        # Strip <reasoning>...</reasoning> tags from streaming text
                        if delta_content and ctx.reasoning_stripper:
                            delta_content = ctx.reasoning_stripper.process(delta_content)
                        if delta_content:
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

                            # Validate tool name against request tools (detect hallucinated names).
                            # CC workflow tools (_CC_WORKFLOW_TOOLS) are always allowed — they may
                            # appear in conversation context even when absent from this request's
                            # system prompt (e.g. ExitPlanMode called during a BUILD turn).
                            if ctx.valid_names and name not in _CC_WORKFLOW_TOOLS and not validate_tool_name(name, ctx.valid_names):
                                metrics.increment_tool_counter("hallucinated")
                                if ctx.model_id:
                                    metrics.record_model_event(ctx.model_id, "tool_hallucination")
                                print(f"[streaming] WARNING: Hallucinated tool name '{name}' from {ctx.model_id}")
                                ctx.last_tool_index -= 1  # undo the increment above
                                ctx.hallucinated_tool_indices.add(current_index)
                                continue

                            metrics.increment_tool_counter("native")
                            if ctx.model_id:
                                metrics.record_model_event(ctx.model_id, "tool_success")
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
                            if current_index in ctx.hallucinated_tool_indices:
                                pass  # skip arguments for hallucinated tools — no block was started
                            else:
                                if ctx.last_tool_index not in ctx.tool_args_buffer:
                                    ctx.tool_args_buffer[ctx.last_tool_index] = ""
                                ctx.tool_args_buffer[ctx.last_tool_index] += arguments
                                yield sse.input_json_delta(ctx.last_tool_index, arguments)

                # ── Finish ───────────────────────────────────────────
                if finish_reason and not ctx.has_sent_stop_reason:
                    ctx.has_sent_stop_reason = True

                    for ev in await _flush_xml_buffer(ctx):
                        yield ev

                    # Safety net: catch <tool_call> XML in accumulated text or
                    # reasoning buffer that the XmlToolBuffer missed.
                    # Also catches truncated tool calls when prior XML tools were already emitted.
                    safety_text = ctx.accumulated_text + ctx.reasoning_buffer
                    if "<tool_call" in safety_text:
                        safety_tools, _ = extract_tool_calls_from_text(
                            safety_text, valid_tool_names=ctx.valid_names, tools=ctx.request_tools,
                        )
                        if safety_tools:
                            print(f"[streaming] SAFETY NET: {len(safety_tools)} tool(s) in accumulated text "
                                  f"that buffer missed!", flush=True)
                            for tc in safety_tools:
                                for ev in _emit_xml_tool(ctx, tc["name"], tc["input"]):
                                    yield ev
                        elif finish_reason == "length":
                            # Truncated tool call that couldn't be recovered —
                            # warn CC so the user knows an action was dropped
                            metrics.increment_tool_counter("truncated")
                            warning = (
                                "\n\n[proxy-warning: A tool call was truncated due to output length limits. "
                                "The previous tool calls executed but an additional tool call was cut off. "
                                "Please retry with the remaining action.]"
                            )
                            if ctx.text_block_closed:
                                # Reopen a new text block for the warning
                                ctx.last_tool_index += 1
                                idx = ctx.last_tool_index
                                yield sse.content_block_start_text(idx)
                                yield sse.text_delta(idx, warning)
                                yield sse.content_block_stop(idx)
                            else:
                                ctx.accumulated_text += warning
                            print(f"[streaming] WARNING: Emitted truncation warning for dropped tool call "
                                  f"({len(ctx.accumulated_text)} chars in accumulated_text)", flush=True)

                    tool_events, valid_tool_blocks = _close_native_tool_blocks(ctx, finish_reason)
                    for ev in tool_events:
                        yield ev
                    for ev in _process_reasoning_buffer(ctx):
                        yield ev
                    for ev in _close_text_block(ctx):
                        yield ev

                    # Estimate output tokens if provider didn't report them
                    if ctx.output_tokens == 0:
                        ctx.output_tokens = _estimate_output_tokens(ctx)

                    stop_reason = _compute_stream_stop_reason(ctx, finish_reason, valid_tool_blocks)
                    # Store thinking chars on request for cost tracking by _tracked_stream
                    if ctx.thinking_chars > 0:
                        setattr(original_request, "_thinking_chars", ctx.thinking_chars)
                    print(f"[streaming] CLOSE: stop_reason={stop_reason} finish_reason={finish_reason} "
                          f"tool_index={ctx.tool_index} has_xml={ctx.has_xml_tool_calls} "
                          f"no_tools={no_tools_mode} output_tokens={ctx.output_tokens} "
                          f"thinking_chars={ctx.thinking_chars}", flush=True)
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

            # Safety net (fallback path) — check both accumulated text and reasoning buffer
            safety_text = ctx.accumulated_text + ctx.reasoning_buffer
            if "<tool_call" in safety_text:
                safety_tools, _ = extract_tool_calls_from_text(
                    safety_text, valid_tool_names=ctx.valid_names, tools=ctx.request_tools,
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

            # Estimate output tokens if provider didn't report them
            if ctx.output_tokens == 0:
                ctx.output_tokens = _estimate_output_tokens(ctx)

            fallback_stop = "tool_use" if ctx.has_any_tools else "end_turn"
            if ctx.thinking_chars > 0:
                setattr(original_request, "_thinking_chars", ctx.thinking_chars)
            print(f"[streaming] FALLBACK CLOSE: stop_reason={fallback_stop} "
                  f"tool_index={ctx.tool_index} has_xml={ctx.has_xml_tool_calls} "
                  f"no_tools={no_tools_mode} output_tokens={ctx.output_tokens} "
                  f"thinking_chars={ctx.thinking_chars}", flush=True)
            for ev in _emit_stream_end(ctx, fallback_stop, model_context_window):
                yield ev

    except Exception as e:
        print(f"[streaming] FATAL stream error: {type(e).__name__}: {e}", flush=True)
        yield sse.message_delta("end_turn", 0)
        yield sse.message_stop()
        yield sse.done()
    finally:
        # Resource leak fix: ensure the response generator is closed properly
        # If it's an async generator (litellm streaming response), close it to avoid
        # leaving HTTP connections open
        if hasattr(response_generator, "aclose"):
            try:
                await response_generator.aclose()
            except Exception:
                pass  # Already closed or failed, ignore


# ── Passthrough XML tool extraction ──────────────────────────────────

async def passthrough_xml_tool_extraction(stream_gen: Any, request: Any):
    """Extract GLM argkv <tool_call> XML embedded in passthrough SSE text_delta events.

    The passthrough relay forwards raw Anthropic SSE lines from Z.AI one line at a
    time (via aiter_lines). When GLM-4.7 emits tool calls as embedded XML in text
    content, this wrapper intercepts those text_delta events and converts them to
    proper tool_use block SSE events — exactly as handle_streaming() does for
    LiteLLM streams.

    Key constraint: passthrough yields lines individually (no blank-line separator),
    so ``event: ...`` and ``data: ...`` lines arrive as separate chunks. We buffer
    the ``event:`` line and decide whether to forward or suppress it based on what
    the following ``data:`` line contains.

    Fast path: when no ``<tool_call`` is ever detected, every chunk passes through
    unchanged (zero-overhead for models that use native tool_use blocks).
    """
    raw_tools = getattr(request, "tools", None) or []
    if not raw_tools:
        async for chunk in stream_gen:
            yield chunk
        return

    valid_names = _build_valid_tool_names(raw_tools)
    buf = XmlToolBuffer(valid_tool_names=valid_names, tools=raw_tools)

    text_block_index: int = 0   # index of the open text content block from upstream
    next_tool_index: int = 1    # next available index for tool_use blocks
    text_block_closed: bool = False  # True once we emit content_block_stop for text block
    activated: bool = False     # True once any <tool_call XML is seen (stays True)
    pending_event_line: str | None = None  # buffered "event: ...\n" line

    async for chunk in stream_gen:
        # Buffer event: lines — yield decision deferred to the data: line
        if chunk.startswith("event: "):
            pending_event_line = chunk
            continue

        if not chunk.startswith("data: "):
            # Non-event, non-data line — flush pending event line and pass through
            if pending_event_line:
                yield pending_event_line
                pending_event_line = None
            yield chunk
            continue

        data_str = chunk[6:].strip()
        if data_str in ("[DONE]", ""):
            if pending_event_line:
                yield pending_event_line
                pending_event_line = None
            yield chunk
            continue

        try:
            data = json.loads(data_str)
        except (ValueError, json.JSONDecodeError):
            if pending_event_line:
                yield pending_event_line
                pending_event_line = None
            yield chunk
            continue

        evt_type = data.get("type", "")

        if evt_type == "content_block_start":
            block = data.get("content_block", {})
            if block.get("type") == "text":
                text_block_index = data.get("index", 0)
                next_tool_index = text_block_index + 1
            if pending_event_line:
                yield pending_event_line
                pending_event_line = None
            yield chunk

        elif evt_type == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                # Fast path: no XML activity — pass through unchanged
                if not activated and "<tool_call" not in text:
                    if pending_event_line:
                        yield pending_event_line
                        pending_event_line = None
                    yield chunk
                    continue

                if "<tool_call" in text:
                    activated = True

                # Slow path: suppress original event:+data: pair, emit transformed events
                pending_event_line = None  # discard buffered event: line

                for seg in buf.feed(text):
                    if seg["type"] == "text" and seg["text"] and not text_block_closed:
                        yield sse.text_delta(text_block_index, seg["text"])
                    elif seg["type"] == "tool_call":
                        if not text_block_closed:
                            yield sse.content_block_stop(text_block_index)
                            text_block_closed = True
                        for ev in _emit_tool_use_block(seg["name"], seg["input"], next_tool_index):
                            yield ev
                        next_tool_index += 1
            else:
                if pending_event_line:
                    yield pending_event_line
                    pending_event_line = None
                yield chunk

        elif evt_type == "content_block_stop":
            stopped_index = data.get("index")
            if activated and stopped_index == text_block_index:
                # Flush XmlToolBuffer — process any remaining buffered XML
                for seg in buf.flush():
                    if seg["type"] == "text" and seg["text"] and not text_block_closed:
                        yield sse.text_delta(text_block_index, seg["text"])
                    elif seg["type"] == "tool_call":
                        if not text_block_closed:
                            yield sse.content_block_stop(text_block_index)
                            text_block_closed = True
                        for ev in _emit_tool_use_block(seg["name"], seg["input"], next_tool_index):
                            yield ev
                        next_tool_index += 1
                    elif seg["type"] == "incomplete_tool_call":
                        partial_xml = seg.get("text", "")
                        logger.warning(
                            "[passthrough-xml] incomplete_tool_call at stream end (%d chars) — attempting recovery",
                            len(partial_xml),
                        )
                        # Attempt 3-level recovery (deterministic → LLM → strip)
                        _recovery_model = os.environ.get("CLASSIFIER_MODEL", "openai/deepseek-chat")
                        _recovery_key = (
                            os.environ.get("CLASSIFIER_API_KEY")
                            or os.environ.get("ANTHROPIC_API_KEY", "")
                        )
                        recovered = await recover_incomplete_tool_call(
                            partial_xml=partial_xml,
                            tools=raw_tools,
                            model=_recovery_model,
                            api_key=_recovery_key,
                        )
                        if recovered:
                            if not text_block_closed:
                                yield sse.content_block_stop(text_block_index)
                                text_block_closed = True
                            for tc in recovered:
                                for ev in _emit_tool_use_block(tc["name"], tc["input"], next_tool_index):
                                    yield ev
                                next_tool_index += 1
                            logger.info(
                                "[passthrough-xml] recovered %d tool(s) from incomplete XML",
                                len(recovered),
                            )
                        else:
                            # Final fallback: emit clean text so model sees what it produced
                            clean = strip_tool_call_xml(partial_xml)
                            if clean and not text_block_closed:
                                yield sse.text_delta(text_block_index, clean)
                            logger.warning(
                                "[passthrough-xml] recovery failed — emitting %d chars as text",
                                len(clean) if clean else 0,
                            )

                if text_block_closed:
                    # We already emitted our own content_block_stop — suppress upstream's
                    pending_event_line = None
                else:
                    # Normal text-only response end — pass through
                    if pending_event_line:
                        yield pending_event_line
                        pending_event_line = None
                    yield chunk
            else:
                if pending_event_line:
                    yield pending_event_line
                    pending_event_line = None
                yield chunk

        else:
            # All other events (message_start, message_delta, message_stop, etc.)
            if pending_event_line:
                yield pending_event_line
                pending_event_line = None
            yield chunk


# ── Stream quality helpers (migrated from stream_quality.py) ────────

async def accumulate_stream(
    stream_generator,
) -> tuple[str, list[str], list[str]]:
    """Consume a streaming generator and extract text + tool names.

    Returns: (accumulated_text, raw_chunks, tool_names)
    - raw_chunks: SSE event strings for replay if quality is good
    - accumulated_text: extracted text content for quality evaluation
    - tool_names: names of tool_use blocks detected (not dummy "unknown")
    """
    chunks: list[str] = []
    text_parts: list[str] = []
    tool_names: list[str] = []

    try:
        async for chunk in stream_generator:
            chunks.append(chunk)
            for line in chunk.split("\n"):
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                except (ValueError, json.JSONDecodeError):
                    continue
                evt_type = data.get("type", "")
                if evt_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        text_parts.append(text)
                        # Detect XML tool calls from GLM-4.7 / Z.AI passthrough.
                        # Path A (handle_streaming + XmlToolBuffer) already converts these
                        # to native SSE for CC. This path only needs accurate counts for
                        # quality heuristics so scores don't falsely hit 0.00.
                        if "<invoke name=" in text or "<tool_call" in text or "\uff5cDSML\uff5c" in text:
                            for m in re.finditer(
                                r'<invoke\s+name=["\']([^"\']+)["\']'
                                r'|<tool_call\s+name=["\']([^"\']+)["\']'
                                r'|\uff5cDSML\uff5cinvoke\s+name=["\']([^"\']+)["\']',
                                text,
                            ):
                                tool_names.append(m.group(1) or m.group(2) or m.group(3))
                elif evt_type == "content_block_start":
                    block = data.get("content_block", {})
                    if block.get("type") == "tool_use":
                        tool_names.append(block.get("name", "unknown"))
    except Exception as e:  # noqa: BLE001
        # Upstream timed out or errored mid-stream.
        # Return whatever we accumulated so the quality pipeline can replay partial content
        # instead of propagating the exception and giving the client a clean disconnect.
        logger.warning(
            "[accumulate-stream] upstream interrupted (%s: %s) — returning %d partial chunks",
            type(e).__name__, e, len(chunks),
        )

    # Strip XML tool blocks from accumulated text so quality heuristics score prose only.
    # H7/H17 penalize XML as "unverified claims" — removing it prevents false 0.00 scores.
    # IMPORTANT: join first, then strip — XML blocks span multiple SSE chunks so per-chunk
    # regex would never match the full <invoke>...</invoke> pattern.
    if tool_names:
        full_text = "".join(text_parts)
        full_text = re.sub(r"<function_calls>.*?</function_calls>", "", full_text, flags=re.DOTALL)
        full_text = re.sub(r"<tool_call[^>]*>.*?</tool_call>", "", full_text, flags=re.DOTALL)
        full_text = re.sub(r"<invoke[^>]*>.*?</invoke>", "", full_text, flags=re.DOTALL)
        # Also strip DSML format: <｜DSML｜invoke ...>...</｜DSML｜invoke>
        full_text = re.sub(r"\uff5cDSML\uff5cinvoke[^>]*>.*?\uff5cDSML\uff5c/invoke>", "", full_text, flags=re.DOTALL)
        text_parts.clear()
        if full_text.strip():
            text_parts.append(full_text)

    return "".join(text_parts), chunks, tool_names


async def tracked_stream(
    stream_gen,
    request: Any,
    ctx: Any,
    cfg_obj: Any,
):
    """Wrap a stream generator to capture post-stream metrics.

    Parses SSE events as they pass through to accumulate text and tool counts,
    then updates the most recent streaming RequestLog with quality score and
    output tokens after the stream completes.
    """
    

    text_parts: list[str] = []
    tool_names: list[str] = []
    output_tokens = 0
    input_tokens = 0
    thinking_chars = 0

    async for chunk in stream_gen:
        yield chunk
        for line in chunk.split("\n"):
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                continue
            try:
                data = json.loads(data_str)
            except (ValueError, json.JSONDecodeError):
                continue
            evt_type = data.get("type", "")
            if evt_type == "message_start":
                usage = data.get("message", {}).get("usage", {})
                if usage.get("input_tokens"):
                    input_tokens = usage["input_tokens"]
            elif evt_type == "content_block_delta":
                delta = data.get("delta", {})
                delta_type = delta.get("type", "")
                if delta_type == "text_delta":
                    text_parts.append(delta.get("text", ""))
                elif delta_type == "thinking_delta":
                    thinking_chars += len(delta.get("thinking", ""))
            elif evt_type == "content_block_start":
                block = data.get("content_block", {})
                if block.get("type") == "tool_use":
                    tool_names.append(block.get("name", "unknown"))
            elif evt_type == "message_delta":
                usage = data.get("usage", {})
                if usage.get("output_tokens"):
                    output_tokens = usage["output_tokens"]

    # Post-stream: evaluate quality
    text = "".join(text_parts)
    tool_calls = [{"type": "tool_use", "name": n} for n in tool_names]
    q_score, q_issues = score_response_quality(
        ctx.intent, text, tool_calls, is_analysis=ctx.is_analysis,
        input_tokens=input_tokens,
    )
    ctx.quality_score = q_score
    ctx.quality_issues = q_issues

    # P2: Record quality in adaptive routing window
    metrics.update_model_quality(
        model=request.model,
        quality_score=q_score,
        grounding_score=ctx.grounding_score,
        intent=ctx.intent,
    )

    # Post-stream: classifier outcome validation
    from llm.transformers.quality_refinement import _validate_intent_outcome
    outcome_correct = _validate_intent_outcome(ctx.intent, text, tool_calls, output_tokens)
    if outcome_correct:
        metrics.increment_intent_outcome_correct()
    else:
        metrics.increment_intent_outcome_wrong()
        metrics.record_model_event("classifier", f"outcome_wrong_{ctx.intent}")

    # Legacy classifier mismatch validation
    if ctx.intent == "CHAT" and len(tool_names) > 3:
        metrics.record_model_event("classifier", "validated_wrong_chat")
    elif ctx.intent == "BUILD" and len(text) > 1000 and not tool_names:
        metrics.record_model_event("classifier", "validated_wrong_build")

    # Estimate cost
    cost = cfg_obj.model_costs.cost_usd(request.model, 0, output_tokens)
    fallback_thinking_chars = getattr(request, "_thinking_chars", 0)
    effective_thinking_chars = thinking_chars or fallback_thinking_chars
    thinking_tokens = max(0, effective_thinking_chars // 4) if effective_thinking_chars else 0

    metrics.update_streaming_log(
        output_tokens=output_tokens,
        quality_score=q_score,
        cost_usd=cost,
        thinking_tokens=thinking_tokens,
    )
    if thinking_tokens > 0:
        logger.debug(
            "[stream-quality] thinking_tokens=%d chars=%d model=%s",
            thinking_tokens, effective_thinking_chars, request.model,
        )

    if q_score < 0.7:
        logger.info(
            "[stream-quality] score=%.2f issues=%s model=%s intent=%s input_tokens=%d",
            q_score, q_issues, request.model, ctx.intent, input_tokens,
        )

    # Post-stream: run grounding validation for analysis responses
    # This validates citations against tool results AFTER streaming completes
    # session_id is in ctx (set in server.py), NOT in the request object
    session_id = getattr(ctx, "session_id", None)
    if ctx.is_analysis and session_id and text.strip():
        # Run grounding validation asynchronously (non-blocking)
        # This validates citations, extracts code snippets, and tracks multi-hop relationships
        asyncio.create_task(_run_post_stream_validation(
            accumulated_text=text,
            tool_names=tool_names,
            request=request,
            ctx=ctx,
            cfg=cfg_obj,
            session_id=session_id,
        ))


# ── StreamEventTransformer ───────────────────────────────────────────

class StreamEventTransformer(Transformer):
    """
    AGNOSTIC transformer for handling streaming SSE events.

    Initializes streaming context state on TransformContext before streaming
    begins. The actual chunk-by-chunk processing happens in handle_streaming()
    above, which reads this initialized state.

    AGNOSTIC DESIGN REQUIREMENT:
    - Zero model-specific if/elif blocks (no model_name checks)
    - Zero hardcoded model patterns
    - Same behavior for ALL models
    """

    @property
    def name(self) -> str:
        return "stream_event"

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

        # AGNOSTIC event tracking state
        self._event_count = 0
        self._content_block_count = 0
        self._text_delta_count = 0
        self._error_count = 0

    async def transform(self, request: object, ctx: TransformContext) -> None:
        """
        Initialize AGNOSTIC streaming event handling infrastructure.

        Sets up ctx fields that handle_streaming() reads when building _StreamCtx.
        """
        if not self.enabled:
            return

        # Only process requests with streaming enabled
        if not getattr(request, "stream", False):
            return

        self._initialize_streaming_state(request, ctx)
        logger.debug("[stream-event] Initialized streaming state for request")

    def _initialize_streaming_state(self, request: object, ctx: TransformContext) -> None:
        """
        Initialize AGNOSTIC streaming state for processing.

        _ReasoningStripper is now defined in this module — no backwards import.
        """
        # Initialize event tracking counters
        setattr(request, "streaming_event_count", 0)
        setattr(request, "streaming_content_blocks", [])
        setattr(request, "streaming_text_deltas", [])
        setattr(request, "streaming_errors", [])

        # Initialize reasoning buffer (AGNOSTIC - works for all models)
        if not hasattr(ctx, "reasoning_buffer"):
            ctx.reasoning_buffer = ""
        if not hasattr(ctx, "reasoning_stripper"):
            ctx.reasoning_stripper = _ReasoningStripper()  # defined in this file

        # Initialize text block tracking
        if not hasattr(ctx, "text_block_open"):
            ctx.text_block_open = False
        if not hasattr(ctx, "accumulated_text"):
            ctx.accumulated_text = ""

        logger.debug(
            "[stream-event] Initialized streaming infrastructure: "
            "event_tracking=True, content_blocks=True, reasoning=True"
        )

    def normalize_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize event data to AGNOSTIC format."""
        if not self.enabled:
            return event_data

        event_type = event_data.get("event", "")
        normalized = {
            "type": event_type,
            "delta": event_data.get("delta", {}),
            "data": event_data.get("data", {}),
            "timestamp": event_data.get("timestamp"),
        }

        self._event_count += 1
        if "content_block" in event_type:
            self._content_block_count += 1
        elif "text_delta" in event_type:
            self._text_delta_count += 1
        elif event_type == "error":
            self._error_count += 1

        return normalized

    def validate_event_sequence(self, events: List[Dict[str, Any]]) -> List[str]:
        """Validate AGNOSTIC event sequence rules."""
        if not self.enabled:
            return []

        errors = []
        open_blocks = sum(1 for e in events if e.get("type") == EVENT_CONTENT_BLOCK_START)
        close_blocks = sum(1 for e in events if e.get("type") == EVENT_CONTENT_BLOCK_STOP)
        if open_blocks != close_blocks:
            errors.append(f"Content block mismatch: {open_blocks} starts but {close_blocks} stops")
        if events and events[-1].get("type") != EVENT_MESSAGE_STOP:
            errors.append(f"Event sequence should end with {EVENT_MESSAGE_STOP}")
        error_count = sum(1 for e in events if e.get("type") == EVENT_ERROR)
        if error_count > 0:
            errors.append(f"Found {error_count} error events in sequence")
        return errors

    def get_streaming_metrics(self) -> Dict[str, Any]:
        """Get AGNOSTIC streaming metrics."""
        return {
            "total_events": self._event_count,
            "content_blocks": self._content_block_count,
            "text_deltas": self._text_delta_count,
            "errors": self._error_count,
            "enabled": self.enabled,
        }

    def reset_metrics(self) -> None:
        """Reset AGNOSTIC streaming metrics."""
        self._event_count = 0
        self._content_block_count = 0
        self._text_delta_count = 0
        self._error_count = 0
