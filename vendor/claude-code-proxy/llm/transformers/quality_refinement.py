"""
Quality Refinement Transformer

AGNOSTIC transformer for quality scoring and refinement decisions.

Extracts and consolidates quality evaluation logic from stream_quality.py:
- Quality scoring thresholds (AGNOSTIC, no model-specific)
- Phase-based decisions (READ vs SYNTHESIZING, AGNOSTIC, no model-specific)
- Tool-heavy response detection (AGNOSTIC, no model-specific)
- Quality-based feedback generation (AGNOSTIC, no model-specific)

CRITICAL DESIGN REQUIREMENT: AGNOSTIC (NO MODEL-SPECIFIC LOGIC)
- Zero checks of model_name, model patterns, or provider quirks
- Same refinement rules for ALL models
- Future-proof: New models automatically supported

This is part of the architecture refactoring to eliminate scattered
model-specific logic across multiple files (stream_quality.py, etc.).
"""
from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from llm.pipeline import Transformer, TransformContext
from utils.quality import score_response as score_response_quality
from llm.transformers.stream_event import accumulate_stream

if TYPE_CHECKING:
    from config import ProxyConfig

logger = logging.getLogger(__name__)


# ── Stateless quality helpers (migrated from stream_quality.py) ──────

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


def _validate_intent_outcome(intent: str, text: str, tool_calls: list, output_tokens: int) -> bool:
    """Return True if the response behavior matches the classified intent.

    Used to build outcome_accuracy_pct — a proxy for classifier correctness
    based on observable response behavior, not LLM-regex agreement.
    """
    has_tools = bool(tool_calls)
    text_len = len(text)

    if intent in ("READ", "SYNTHESIZING", "PLAN"):
        return text_len > 800 or (text_len > 100 and has_tools)
    elif intent == "BUILD":
        return has_tools or text_len > 200
    elif intent == "VERIFY":
        return has_tools or text_len > 100
    elif intent == "CHAT":
        return text_len < 7000
    return True  # Unknown intent — don't penalize


class QualityRefinementTransformer(Transformer):
    """
    AGNOSTIC transformer for quality scoring and refinement decisions.

    Extracts and consolidates quality evaluation logic from stream_quality.py:
    - Quality scoring thresholds (AGNOSTIC, no model-specific)
    - Phase-based decisions (READ vs SYNTHESIZING, AGNOSTIC, no model-specific)
    - Tool-heavy response detection (AGNOSTIC, no model-specific)
    - Quality-based feedback generation (AGNOSTIC, no model-specific)

    AGNOSTIC DESIGN REQUIREMENT:
    - Zero model-specific if/elif blocks (no model_name checks)
    - Same refinement rules for ALL models
    - Future-proof: New models automatically supported

    This replaces scattered model-specific quality logic in stream_quality.py
    with a centralized, AGNOSTIC configuration system.
    """

    @property
    def name(self) -> str:
        return "quality_refinement"

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

        # AGNOSTIC quality thresholds (same for ALL models, no model-specific)
        self.quality_threshold = 0.70  # Default threshold
        self.max_refinements = 3  # Default max refinements

    async def transform(self, request: object, ctx: TransformContext) -> None:
        """
        Evaluate quality and decide if refinement is needed.

        AGNOSTIC: Applies to ALL models, no model-specific logic.

        Returns None (modifies request messages in-place).
        """
        if not self.enabled:
            return

        # Skip refinement during READ phase (model is still gathering data)
        # AGNOSTIC: No model-specific checks
        if ctx.analysis_phase == "READ":
            logger.debug(
                f"[quality-refinement] SKIP: analysis_phase=READ — intermediate response, no refinement"
            )
            return

        # Skip refinement if no request messages to modify
        # AGNOSTIC: No model-specific checks
        if not hasattr(request, "messages") or request.messages is None:
            logger.debug(
                f"[quality-refinement] SKIP: no messages to refine"
            )
            return

        # Accumulate full response content for scoring
        # AGNOSTIC: No model-specific logic
        text = "".join(
            m.get("text", "") if isinstance(m, dict) else str(m) for m in request.messages
        )
        tool_calls = getattr(request, "tool_calls_from_reasoning", []) or []

        # Evaluate quality using unified scorer
        # AGNOSTIC: Same scoring for ALL models, no model-specific adjustments
        score, issues = score_response_quality(
            ctx.intent, text, tool_calls, is_analysis=True
        )

        logger.debug(
            f"[quality-refinement] Quality score: {score:.2%} | Threshold: {self.quality_threshold:.0%} | Issues: {', '.join(issues)}"
        )

        # AGNOSTIC refinement rules (same for ALL models):
        is_good_enough = score >= self.quality_threshold

        if is_good_enough:
            # Quality is acceptable - no refinement needed
            logger.info(
                f"[quality-refinement] SKIP: score {score:.2%} >= threshold {self.quality_threshold:.0%} — good enough quality"
            )
            return

        # Tool-heavy response detection (AGNOSTIC, no model-specific logic)
        # Skip refinement for tool-heavy responses (model is mid-execution)
        # AGNOSTIC: Same rule for ALL models
        tool_call_count = len(tool_calls)
        text_len = len(text.strip())
        text_ratio = text_len / max(text_len + tool_call_count * 200, 1)
        if text_ratio < 0.3:
            logger.info(
                f"[quality-refinement] SKIP: tool_call_count={tool_call_count} text_len={text_len} text_ratio={text_ratio:.2f} — tool-heavy, no refinement"
            )
            return

        # Determine refinement attempt
        current_refinements = getattr(ctx, "refinement_count", 0)
        if current_refinements >= self.max_refinements:
            logger.info(
                f"[quality-refinement] SKIP: max refinements {self.max_refinements} reached"
            )
            return

        # Refine response (AGNOSTIC: same process for ALL models)
        logger.info(
            f"[quality-refinement] REFINING: score={score:.2f} threshold={self.quality_threshold:.0f} issues={', '.join(issues)}"
        )

        # Generate refinement feedback (AGNOSTIC, no model-specific messages)
        # Generic feedback based on quality issues, not model name checks
        feedback_parts = [
            f"[quality-refinement] Score: {score:.2%} | Threshold: {self.quality_threshold:.0%}",
            f"Issues: {', '.join(issues)}",
        ]

        # Add generic quality guidance (AGNOSTIC, no model-specific)
        if issues:
            guidance = "\n🔍 QUALITY ISSUES DETECTED:\n"
            for issue in issues:
                guidance += f"• {issue}\n"
            guidance += "Consider re-reading the codebase. Cite SPECIFIC locations.\n"
            guidance += "Improve the response to be more detailed and accurate."
            feedback_parts.append(guidance)

        # Update refinement count (AGNOSTIC)
        setattr(ctx, "refinement_count", current_refinements + 1)

        # Add feedback to request messages (append, don't replace)
        if not hasattr(request, "messages") or request.messages is None:
            request.messages = []

        request.messages.append(
            {"role": "assistant", "content": "\n".join(feedback_parts)[:4000]}
        )

        logger.info(
            f"[quality-refinement] Added refinement feedback with {len(feedback_parts)} parts"
        )

        return None


# ── Shared feedback builder (used by both stream and non-stream paths) ──────

def _build_refinement_feedback(
    score: float,
    issues: list[str],
    quality_threshold: float,
    request_messages: list[Any] | None = None,
) -> str:
    """Build human-readable quality feedback for a re-request.

    If request_messages is provided, re-injects the last meaningful user message
    as a REMINDER prefix so the model stays anchored to its original task while
    addressing quality issues. This is agnostic — the proxy just echoes the user's
    own words back; it knows nothing about the task content.
    """
    issue_specific: list[str] = []
    for issue in issues:
        if "factual_verification" in issue:
            continue
        if "specificity" in issue:
            issue_specific.append("- Add (file:line) citations to EVERY claim")
        elif "unverified" in issue or "shallow" in issue:
            issue_specific.append("- Read the files you mentioned before claiming")
        elif "generic" in issue:
            issue_specific.append("- Replace 'handles/manages' with actual code behavior")
        elif "exploration" in issue:
            issue_specific.append("- Use Glob/Grep to find related patterns")
        elif "concrete" in issue:
            issue_specific.append("- Provide line counts, function names, numbers")

    parts = [
        f"[quality-refinement] Score: {score:.0%} | Threshold: {quality_threshold:.0%}",
        f"Issues: {', '.join(issues)}",
    ]
    if issue_specific:
        parts.append("\nMANDATORY FIXES:\n" + "\n".join(issue_specific))
    parts.append("\nRe-read the codebase. Cite SPECIFIC locations. Prove your claims.")

    # Re-inject original task intent so quality feedback doesn't displace it.
    # Uses get_last_user_text() which strips tool results and system reminders.
    if request_messages:
        try:
            from router.llm_router import get_last_user_text
            original_intent = get_last_user_text(request_messages)
            if original_intent:
                reminder = f"REMINDER — complete your original task:\n{original_intent[:800]}"
                parts.insert(0, reminder)
        except Exception:
            pass  # Never let re-injection break the feedback path

    return "\n".join(parts)


# ── Non-streaming quality refinement loop ──────────────────────────────

async def analysis_quality_nonstream(
    anthropic_response: Any,
    request: Any,
    ctx: "TransformContext",
    cfg_obj: "ProxyConfig",
) -> tuple[Any, str]:
    """Non-streaming quality loop: evaluate → refine via re-request if needed.

    Returns (final_anthropic_response, provider_used).
    Canonical home for all non-streaming quality refinement logic.
    """
    # Lazy imports to avoid circular dependency (llm → proxy)
    from proxy.proxy import run_messages
    from llm.converters import convert_litellm_to_anthropic
    from llm.schemas import Message

    max_refinements = cfg_obj.analysis.max_refinements if ctx.is_analysis else 0
    quality_threshold = cfg_obj.analysis.quality_threshold
    provider_used = "primary"

    if max_refinements <= 0:
        return anthropic_response, provider_used

    score, issues = score_anthropic_response(anthropic_response, ctx.intent, is_analysis=True)
    ctx.quality_score = score
    ctx.quality_issues = issues

    for attempt in range(1, max_refinements + 1):
        if score >= quality_threshold:
            break

        logger.info(
            "[refinement] attempt=%d/%d score=%.2f threshold=%.2f issues=%s",
            attempt, max_refinements, score, quality_threshold, issues,
        )
        ctx.refinement_attempt = attempt

        feedback = _build_refinement_feedback(score, issues, quality_threshold,
                                              request_messages=request.messages)
        resp_text = extract_response_text(anthropic_response)

        if not hasattr(request, "messages") or request.messages is None:
            break

        request.messages.append(Message(role="assistant", content=resp_text[:4000]))
        request.messages.append(Message(role="user", content=feedback))

        _, out, provider_used = await run_messages(
            request_obj=request, cfg=cfg_obj, ctx=ctx,
        )
        anthropic_response = convert_litellm_to_anthropic(
            out, request,
            model_context_window=ctx.effective_context_window or cfg_obj.routing.model_context_window,
            strip_reasoning=cfg_obj.policy.strip_reasoning,
        )
        score, issues = score_anthropic_response(anthropic_response, ctx.intent, is_analysis=True)
        ctx.quality_score = score
        ctx.quality_issues = issues

    if ctx.refinement_attempt > 0:
        logger.info(
            "[refinement] done: attempts=%d final_score=%.2f issues=%s",
            ctx.refinement_attempt, ctx.quality_score, ctx.quality_issues,
        )

    return anthropic_response, provider_used


# ── Quality Orchestration (migrated from stream_quality.py) ──────────

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

    # 3.5. Skip refinement during READ phase
    if ctx.analysis_phase == "READ":
        logger.info(
            "[stream-refinement] SKIP: analysis_phase=READ — intermediate response, no refinement",
        )
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

        # Build issue-specific feedback, optionally enriched with file verification
        verification_feedback = []
        if "code_citations_without_verification" in issues:
            target_path = getattr(request, "target_path", "vendor/")
            verification_feedback = await _build_verification_feedback(text, target_path)

        feedback = _build_refinement_feedback(score, issues, quality_threshold,
                                              request_messages=request.messages)
        if verification_feedback:
            feedback = feedback.replace(
                "\nRe-read the codebase.",
                "\n🔍 FILE VERIFICATION:\n" + "\n".join(verification_feedback) + "\nRe-read the codebase.",
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
                out, request,
                model_context_window=ctx.effective_context_window or cfg_obj.routing.model_context_window,
                strip_reasoning=cfg_obj.policy.strip_reasoning,
            )

            new_score, new_issues = score_anthropic_response(anthropic_response, ctx.intent, is_analysis=True)

            # Safety: if refinement is significantly WORSE (>10% drop), keep the original
            if new_score < original_score - 0.10:
                logger.warning(
                    "[stream-refinement] attempt=%d SIGNIFICANTLY WORSE: %.2f < original %.2f - 0.10 — keeping original",
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


async def _build_verification_feedback(
    response_text: str,
    target_path: str,
) -> list[str]:
    """
    Build verification feedback by checking mentioned files against reality.

    Extracts file:line references from the response, checks if they exist,
    and provides specific feedback with alternatives if wrong.
    """
    import os
    import re

    feedback = []

    file_ref_pattern = r'[\w/.-]+\.\w+:\d+'
    mentioned_refs = re.findall(file_ref_pattern, response_text)

    if not mentioned_refs:
        return feedback

    checked_paths = set()
    for file_ref in mentioned_refs[:5]:
        file_path = file_ref.rsplit(':', 1)[0]

        if file_path in checked_paths:
            continue
        checked_paths.add(file_path)

        if os.path.isabs(file_path):
            full_path = file_path
        else:
            full_path = os.path.join(target_path, file_path)

        exists = os.path.exists(full_path)

        if not exists:
            base_name = os.path.basename(file_path)
            name_without_ext = os.path.splitext(base_name)[0]

            alternatives = []
            try:
                from difflib import SequenceMatcher

                candidates = []
                for root, dirs, files in os.walk(target_path):
                    for f in files:
                        f_name_without_ext = os.path.splitext(f)[0]
                        rel_path = os.path.relpath(os.path.join(root, f), target_path)

                        if f_name_without_ext == name_without_ext:
                            alternatives.insert(0, rel_path)
                        else:
                            similarity = SequenceMatcher(None, name_without_ext.lower(),
                                                       f_name_without_ext.lower()).ratio()
                            if similarity >= 0.7:
                                candidates.append((similarity, rel_path))

                    if len(alternatives) >= 3:
                        break

                if not alternatives and candidates:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    alternatives = [path for _, path in candidates[:3]]

            except Exception as e:
                logger.warning(f"[verification] Error searching for alternatives: {e}")

            if alternatives:
                top_alternatives = alternatives[:3]
                feedback.append(
                    f"❌ '{file_path}' no existe. "
                    f"✅ Encontré: {', '.join(top_alternatives)}"
                )
            else:
                feedback.append(
                    f"❌ '{file_path}' no existe. "
                    f"ℹ️ Usa Glob primero para descubrir la estructura real del proyecto."
                )

    return feedback
