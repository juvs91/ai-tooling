# llm/stream_quality.py — Stream quality evaluation and refinement
"""
Extracted from server.py: functions that accumulate SSE streams,
evaluate response quality, and orchestrate refinement loops.

Dependencies flow: utils/quality.py (scoring), utils/metrics.py (recording),
proxy/proxy.py (re-requests during refinement — lazy import to avoid circular).
"""
from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from utils.quality import score_response as score_response_quality
from utils.metrics import metrics

if TYPE_CHECKING:
    from config import ProxyConfig
    from llm.pipeline import TransformContext

logger = logging.getLogger(__name__)


def extract_response_text(anthropic_response: Any) -> str:
    """Extract concatenated text from an Anthropic response's content blocks."""
    content = getattr(anthropic_response, "content", []) or []
    return "\n".join(
        (b.get("text", "") if isinstance(b, dict) else getattr(b, "text", ""))
        for b in content
        if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "text"
    )


def score_anthropic_response(
    anthropic_response: Any, intent: str, is_analysis: bool,
) -> tuple[float, list[str]]:
    """Score an Anthropic response using the unified quality scorer."""
    content = getattr(anthropic_response, "content", []) or []
    text = extract_response_text(anthropic_response)
    tool_calls = [
        (c if isinstance(c, dict) else {"type": getattr(c, "type", None), "name": getattr(c, "name", None), "input": getattr(c, "input", None)})
        for c in content
        if (c.get("type") if isinstance(c, dict) else getattr(c, "type", None)) == "tool_use"
    ]
    return score_response_quality(intent, text, tool_calls, is_analysis=is_analysis)


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

    async for chunk in stream_generator:
        chunks.append(chunk)
        # Parse SSE lines to extract text deltas and tool_use blocks
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
                    text_parts.append(delta.get("text", ""))
            elif evt_type == "content_block_start":
                block = data.get("content_block", {})
                if block.get("type") == "tool_use":
                    tool_names.append(block.get("name", "unknown"))

    return "".join(text_parts), chunks, tool_names


async def analysis_quality_stream(
    stream_generator,
    request: Any,
    ctx: "TransformContext",
    cfg_obj: "ProxyConfig",
):
    """Streaming quality loop: accumulate → evaluate → replay or re-request.

    Yields SSE event strings compatible with StreamingResponse.
    """
    from llm.sse import response_to_sse_events, ping as sse_ping
    from llm.schemas import Message

    quality_threshold = cfg_obj.analysis.quality_threshold
    max_refinements = cfg_obj.analysis.max_refinements

    # 1. Accumulate the full first-attempt stream
    text, chunks, tool_names = await accumulate_stream(stream_generator)
    tool_use_count = len(tool_names)

    # 1.5. Skip refinement for tool-heavy responses (model is mid-execution)
    # Tool calls are normal CC behavior — Claude Code will send tool_results
    # and the model will continue. Refining mid-chain is counterproductive.
    # Only refine text-heavy responses (synthesis/analysis output).
    text_len = len(text.strip())
    text_ratio = text_len / max(text_len + tool_use_count * 200, 1)
    if text_ratio < 0.3:
        logger.info(
            "[stream-refinement] SKIP: tool_use_count=%d text_len=%d text_ratio=%.2f — tool-heavy, no refinement",
            tool_use_count, text_len, text_ratio,
        )
        for chunk in chunks:
            yield chunk
        return

    # 2. Evaluate quality using unified scorer with real tool names
    real_tools = [{"type": "tool_use", "name": n} for n in tool_names]
    score, issues = score_response_quality(ctx.intent, text, real_tools, is_analysis=True)
    ctx.quality_score = score
    ctx.quality_issues = issues

    # 3. If good enough, replay the original chunks
    if score >= quality_threshold or max_refinements <= 0:
        for chunk in chunks:
            yield chunk
        return

    # 4. Quality insufficient — refine via non-streaming re-requests
    # Lazy import to avoid circular dependency (llm → proxy)
    from proxy.proxy import run_messages
    from llm.converters import convert_litellm_to_anthropic

    original_score = score
    logger.info(
        "[stream-refinement] first-pass score=%.2f threshold=%.2f issues=%s — refining",
        score, quality_threshold, issues,
    )

    for attempt in range(1, max_refinements + 1):
        ctx.refinement_attempt = attempt

        # Send pings to keep connection alive during re-request
        yield sse_ping()

        feedback = (
            f"[quality-feedback] Your previous analysis scored {score:.0%}. "
            f"Issues: {', '.join(issues)}. "
            "Re-analyze with more depth. Read files you mentioned but didn't verify. "
            "Provide function signatures, line counts, and concrete evidence."
        )

        if not hasattr(request, "messages") or request.messages is None:
            break
        request.messages.append(Message(role="assistant", content=text[:4000]))
        request.messages.append(Message(role="user", content=feedback))

        # Force non-streaming for refinement attempt
        original_stream = getattr(request, "stream", True)
        request.stream = False

        try:
            _, out, _provider = await run_messages(
                request_obj=request, cfg=cfg_obj, ctx=ctx,
            )

            anthropic_response = convert_litellm_to_anthropic(
                out, request, model_context_window=ctx.effective_context_window or cfg_obj.routing.model_context_window,
            )

            new_score, new_issues = score_anthropic_response(anthropic_response, ctx.intent, is_analysis=True)

            # Safety: if refinement makes things WORSE, keep the original
            if new_score < original_score:
                logger.warning(
                    "[stream-refinement] attempt=%d WORSE: %.2f < original %.2f — keeping original",
                    attempt, new_score, original_score,
                )
                for chunk in chunks:
                    yield chunk
                return

            score = new_score
            issues = new_issues
            ctx.quality_score = score
            ctx.quality_issues = issues

            if score >= quality_threshold:
                logger.info(
                    "[stream-refinement] attempt=%d score=%.2f — threshold met",
                    attempt, score,
                )
                # Convert refined response to SSE events and stream
                model = getattr(request, "original_model", None) or request.model
                in_tokens = anthropic_response.usage.input_tokens if anthropic_response.usage else 0
                for event in response_to_sse_events(anthropic_response, model, in_tokens):
                    yield event
                return

            # Update text for next iteration's feedback
            text = extract_response_text(anthropic_response)

        finally:
            request.stream = original_stream

    # Exhausted refinements — stream the last refined response (or replay original)
    logger.info(
        "[stream-refinement] exhausted refinements=%d final_score=%.2f issues=%s",
        max_refinements, ctx.quality_score, ctx.quality_issues,
    )
    try:
        model = getattr(request, "original_model", None) or request.model
        in_tokens = anthropic_response.usage.input_tokens if anthropic_response.usage else 0
        for event in response_to_sse_events(anthropic_response, model, in_tokens):
            yield event
    except NameError:
        # anthropic_response never set (messages was None), replay originals
        for chunk in chunks:
            yield chunk


def _validate_intent_outcome(intent: str, text: str, tool_calls: list, output_tokens: int) -> bool:
    """Return True if the response behavior matches the classified intent.

    Used to build outcome_accuracy_pct — a proxy for classifier correctness
    based on observable response behavior, not LLM-regex agreement.
    """
    has_tools = bool(tool_calls)
    text_len = len(text)

    if intent in ("READ", "SYNTHESIZING", "PLAN"):
        # Analysis/planning should produce substantial text OR be mid-execution
        # (short response with tools = gathering data, still valid)
        return text_len > 800 or (text_len > 100 and has_tools)
    elif intent == "BUILD":
        # Building: must either call tools or explain what was done
        return has_tools or text_len > 200
    elif intent == "VERIFY":
        # Verify: should either run tests (tools) or report results
        return has_tools or text_len > 100
    elif intent == "CHAT":
        # Chat: conversational, not excessively long
        return text_len < 7000
    return True  # Unknown intent — don't penalize


async def tracked_stream(
    stream_gen,
    request: Any,
    ctx: "TransformContext",
    cfg_obj: "ProxyConfig",
):
    """Wrap a stream generator to capture post-stream metrics.

    Parses SSE events as they pass through to accumulate text and tool counts,
    then updates the most recent streaming RequestLog with quality score and
    output tokens after the stream completes.
    """
    text_parts: list[str] = []
    tool_names: list[str] = []
    output_tokens = 0
    input_tokens = 0   # Populated from message_start SSE event
    thinking_chars = 0  # Accumulated from thinking_delta events (passthrough + handle_streaming)

    async for chunk in stream_gen:
        yield chunk
        # Parse SSE to accumulate text deltas, tool info, and thinking chars
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
                # Capture input token count for context-aware quality scoring
                usage = data.get("message", {}).get("usage", {})
                if usage.get("input_tokens"):
                    input_tokens = usage["input_tokens"]
            elif evt_type == "content_block_delta":
                delta = data.get("delta", {})
                delta_type = delta.get("type", "")
                if delta_type == "text_delta":
                    text_parts.append(delta.get("text", ""))
                elif delta_type == "thinking_delta":
                    # GLM-4.7 / Anthropic thinking blocks — accumulate char count
                    thinking_chars += len(delta.get("thinking", ""))
            elif evt_type == "content_block_start":
                block = data.get("content_block", {})
                if block.get("type") == "tool_use":
                    tool_names.append(block.get("name", "unknown"))
            elif evt_type == "message_delta":
                usage = data.get("usage", {})
                if usage.get("output_tokens"):
                    output_tokens = usage["output_tokens"]

    # Post-stream: evaluate quality (with input_tokens for context-aware scoring)
    text = "".join(text_parts)
    tool_calls = [{"type": "tool_use", "name": n} for n in tool_names]
    q_score, q_issues = score_response_quality(
        ctx.intent, text, tool_calls, is_analysis=ctx.is_analysis,
        input_tokens=input_tokens,
    )
    ctx.quality_score = q_score
    ctx.quality_issues = q_issues

    # Post-stream: classifier outcome validation — did the response match the intent?
    outcome_correct = _validate_intent_outcome(ctx.intent, text, tool_calls, output_tokens)
    if outcome_correct:
        metrics.increment_intent_outcome_correct()
    else:
        metrics.increment_intent_outcome_wrong()
        metrics.record_model_event("classifier", f"outcome_wrong_{ctx.intent}")

    # Post-stream: legacy classifier mismatch validation
    if ctx.intent == "CHAT" and len(tool_names) > 3:
        metrics.record_model_event("classifier", "validated_wrong_chat")
    elif ctx.intent == "BUILD" and len(text) > 1000 and not tool_names:
        metrics.record_model_event("classifier", "validated_wrong_build")

    # Estimate cost from output tokens + thinking tokens.
    # thinking_chars was accumulated from thinking_delta SSE events above — works for
    # both passthrough (GLM-4.7) and LiteLLM (handle_streaming) paths without needing
    # the _thinking_chars attribute hack. Fall back to attribute if somehow non-zero.
    cost = cfg_obj.model_costs.cost_usd(request.model, 0, output_tokens)
    fallback_thinking_chars = getattr(request, "_thinking_chars", 0)
    effective_thinking_chars = thinking_chars or fallback_thinking_chars
    thinking_tokens = max(0, effective_thinking_chars // 4) if effective_thinking_chars else 0

    # Update the streaming log entry
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
