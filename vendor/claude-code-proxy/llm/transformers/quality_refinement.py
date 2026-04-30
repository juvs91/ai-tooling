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

import asyncio
import httpx
import json
import logging
import os
import re
from difflib import SequenceMatcher
from types import SimpleNamespace
from typing import Any, Optional, TYPE_CHECKING

from llm.converters import convert_litellm_to_anthropic
from llm.compressor import append_session_quality, get_session_quality_history
from llm.pipeline import TransformContext
from llm.schemas import Message
from llm.sse import response_to_sse_events, ping as sse_ping
from llm.transformers.stream_event import accumulate_stream
from utils.quality import score_response as score_response_quality
from utils.tool_utils import _CC_WORKFLOW_TOOL_NAMES as _CC_WORKFLOW_TOOLS

if TYPE_CHECKING:
    from config import ProxyConfig

logger = logging.getLogger(__name__)


# ── Session quality persistence (Item 4 — quality feedback loop) ─────

def _fire_quality_persist(ctx: TransformContext, issues: list[str]) -> None:
    """Fire-and-forget: persist quality score + stub count into SessionCache.

    Uses asyncio.create_task so it never blocks the streaming response path.
    Safe to call from any async context; silently skips if no session_id.
    """
    session_id = getattr(ctx, "session_id", None)
    if not session_id:
        return

    stub_delta = sum(
        1 for issue in issues
        if "stub_implementations" in issue or "stubbed_functions" in issue
    )

    async def _persist() -> None:
        try:
            await append_session_quality(session_id, ctx.quality_score, stub_delta)
            logger.debug(
                "[quality-persist] session=%s score=%.2f stub_delta=%d",
                session_id[:8], ctx.quality_score, stub_delta,
            )
        except Exception as exc:
            logger.warning("[quality-persist] failed: %s", exc)

    try:
        asyncio.create_task(_persist())
    except RuntimeError:
        pass  # No running event loop — harmless in test contexts


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


# ── Two-tier quality gate helpers ────────────────────────────────────────────

async def _llm_quality_gate(
    score: float,
    issues: list[str],
    intent: str,
    response_text: str,
    cfg_obj: "ProxyConfig",
) -> bool:
    """Call the classifier LLM to judge ambiguous quality scores.

    Returns True if refinement is needed, False to skip.
    Only called for scores in the ambiguous zone [certainty_floor, quality_threshold).
    Uses the classifier endpoint (DeepSeek-chat) — cheap, fast, no circular deps.
    """
    try:
        prompt = (
            f"Task intent: {intent}\n\n"
            f"Model response (first 800 chars):\n{response_text[:800]}\n\n"
            f"Quality heuristics flagged: {', '.join(issues) if issues else 'none'}\n"
            f"Heuristic score: {score:.2f} / 1.0\n\n"
            "Does this response adequately address the task? "
            "Reply with a single word only: PASS or REFINE."
        )
        headers = {
            "Authorization": f"Bearer {cfg_obj.classifier.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": cfg_obj.classifier.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 5,
            "temperature": 0.0,
        }
        base_url = (cfg_obj.classifier.base_url or "https://api.openai.com/v1").rstrip("/")
        url = base_url + "/chat/completions"
        timeout = min(cfg_obj.classifier.timeout, 8.0)

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=body, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            answer = data["choices"][0]["message"]["content"].strip().upper()

        should_refine = "REFINE" in answer
        logger.info("[quality-gate] verdict=%s score=%.2f issues=%s", answer, score, issues)
        return should_refine

    except Exception as e:
        logger.warning("[quality-gate] LLM gate failed (%s) — defaulting to REFINE", e)
        return True  # safe default: if gate fails, allow refinement


async def _should_refine(
    score: float,
    issues: list[str],
    intent: str,
    response_text: str,
    cfg_obj: "ProxyConfig",
    ctx: Optional[TransformContext] = None,
) -> bool:
    """Two-tier refinement decision gate with proactive degradation detection.

    Tier 0 — proactive degradation (Priority 3):
      Rolling quality_history delta < -0.15 over last 6 turns → proactive refine
    Tier 1 — deterministic (0ms):
      score >= quality_threshold  → False (definitely good, skip)
      score <  certainty_floor    → True  (definitely bad, refine)
    Tier 2 — LLM judgment (3-6s, only in ambiguous zone):
      [certainty_floor, quality_threshold) + llm_score_gate=True → ask LLM

    Returns True if refinement should proceed.
    """
    # Tier 0: proactive degradation — catch slow drift before it becomes obvious
    if ctx is not None and getattr(ctx, "session_id", None):
        try:
            quality_scores, _ = await get_session_quality_history(ctx.session_id)
            if len(quality_scores) >= 6:
                recent = quality_scores[-3:]
                earlier = quality_scores[-6:-3]
                recent_avg = sum(recent) / len(recent)
                earlier_avg = sum(earlier) / len(earlier)
                delta_avg = recent_avg - earlier_avg
                if delta_avg < -0.15:
                    logger.info(
                        "[quality] PROACTIVE degradation: delta=%.2f session=%s "
                        "(recent=%.2f earlier=%.2f) — forcing refinement",
                        delta_avg, ctx.session_id[:8], recent_avg, earlier_avg,
                    )
                    ctx.degradation_count = getattr(ctx, "degradation_count", 0) + 1
                    return True
        except Exception as exc:
            logger.debug("[quality] Proactive degradation check failed: %s", exc)

    quality_threshold = cfg_obj.analysis.quality_threshold
    certainty_floor = cfg_obj.analysis.score_certainty_floor

    if score >= quality_threshold:
        return False
    if score <= certainty_floor:
        return True
    # Ambiguous zone: use LLM gate if enabled
    if cfg_obj.analysis.llm_score_gate:
        return await _llm_quality_gate(score, issues, intent, response_text, cfg_obj)
    return True  # gate disabled: deterministic fallback


# ── Shared feedback builder (used by both stream and non-stream paths) ──────

def _build_refinement_feedback(
    score: float,
    issues: list[str],
    quality_threshold: float,
    request_messages: list[Any] | None = None,
    intent: str = "",
) -> str:
    """Build human-readable quality feedback for a re-request.

    If request_messages is provided, re-injects the last meaningful user message
    as a REMINDER prefix so the model stays anchored to its original task while
    addressing quality issues. This is agnostic — the proxy just echoes the user's
    own words back; it knows nothing about the task content.
    """
    issue_specific: list[str] = []

    # H18: BUILD-specific stub repair — extract stubbed function names and generate
    # targeted "implement these functions" instructions rather than generic advice.
    if intent in ("BUILD", "BUILDING"):
        stub_count = 0
        stubbed_fns: list[str] = []
        for issue in issues:
            if issue.startswith("stub_implementations("):
                m = re.search(r"\((\d+)_stubs\)", issue)
                if m:
                    stub_count = int(m.group(1))
            elif issue.startswith("stubbed_functions("):
                m = re.search(r"\(([^)]+)\)", issue)
                if m:
                    stubbed_fns = m.group(1).split(",")
        if stub_count > 0:
            stub_lines = [
                f"CRITICAL — {stub_count} stub(s) detected in written code:",
                "  FORBIDDEN: `pass`, `...`, `# TODO`, `raise NotImplementedError`",
                "  REQUIRED: Every function body must contain real, working logic",
            ]
            if stubbed_fns:
                stub_lines.append("  Functions that need full implementation:")
                for fn in stubbed_fns:
                    stub_lines.append(f"    - {fn.strip()}(): replace `pass`/`...` with actual code")
            stub_lines.extend([
                "  STEPS: (1) re-read the file, (2) implement the full logic, (3) verify by reading back",
                "  Do NOT move on until every function has a real implementation.",
            ])
            issue_specific.extend(stub_lines)

    # Detect primary refinement type for observability (first match wins for logging)
    refinement_type = "generic"
    for issue in issues:
        if "stub_implementations" in issue or "stubbed_functions" in issue:
            refinement_type = "stub"
            break
        elif "unverified" in issue:
            refinement_type = "unverified_claims"
            break
        elif "shallow" in issue or "exploration" in issue:
            refinement_type = "shallow_exploration"
            break
        elif "grounding" in issue:
            refinement_type = "grounding"
            break
        elif "specificity" in issue:
            refinement_type = "specificity"
            break

    for issue in issues:
        if "factual_verification" in issue:
            continue
        if "stub_implementations" in issue or "stubbed_functions" in issue:
            continue  # handled above (H18 block)
        if "specificity" in issue:
            issue_specific.append("- Add (file:line) citations to EVERY claim you make")
        elif "unverified" in issue:
            # H7: model made claims without reading files
            m = re.search(r"unverified_claims\(([^)]+)\)", issue)
            if m:
                claim_count = m.group(1)
                issue_specific.append(
                    f"- You made {claim_count} factual claim(s) without reading any files. "
                    "Use Read/Grep to verify each claim before stating it."
                )
            else:
                issue_specific.append(
                    "- Claims detected without tool evidence. "
                    "Read the relevant files first, then cite (file.py:line)."
                )
        elif "shallow" in issue:
            # H6: model mentioned files but didn't read them
            m = re.search(r"shallow_exploration\(mentioned=(\d+),read=(\d+)\)", issue)
            if m:
                mentioned, read = m.group(1), m.group(2)
                issue_specific.append(
                    f"- You mentioned {mentioned} files but only read {read}. "
                    f"Read the remaining {int(mentioned) - int(read)} file(s) before analyzing."
                )
            else:
                issue_specific.append(
                    "- Shallow exploration: you mentioned files you did not read. "
                    "Use Read tool on every file you reference."
                )
        elif "generic" in issue:
            issue_specific.append("- Replace 'handles/manages' with actual code behavior — quote the code")
        elif "exploration" in issue:
            issue_specific.append("- Use Glob/Grep to find related patterns before summarizing")
        elif "concrete" in issue:
            issue_specific.append("- Provide line counts, function names, specific numbers")

    parts = [
        f"[quality-refinement:{refinement_type}] Score: {score:.0%} | Threshold: {quality_threshold:.0%}",
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


def _build_grounding_feedback(
    ctx: TransformContext,
    response_text: str,  # pylint: disable=unused-argument  # Kept for future analysis
    grounding_threshold: float,
) -> str:
    """Build feedback to fix grounding issues."""
    parts = [
        f"[grounding-validation] Score: {ctx.grounding_score:.0%} | Threshold: {grounding_threshold:.0%}",
        "GROUNDING ISSUES:",
    ]

    for issue in ctx.grounding_issues:
        parts.append(f"  - {issue}")

    parts.extend([
        "",
        "MANDATORY FIXES:",
        "  1. For EVERY factual claim, cite (file.py:line) from a file you've READ",
        "  2. If you mention a function/class, QUOTE the code that does what you claim",
        "  3. If you haven't read the file, say 'I need to read this file first'",
        "  4. Never assume file paths, function names, or line numbers",
        "  5. Include code snippets for behavioral claims",
        "",
        "EXAMPLE OF CORRECT CITATION:",
        "The AuthService.validateToken() method checks if the token is expired (auth.py:42):",
        "```python",
        "if token.expiry < now:",
        "    raise InvalidTokenError",
        "```",
        "",
        "Unverified claims will be rejected. Read the files first, then cite them with code snippets.",
    ])

    return "\n".join(parts)


def _build_code_evidence(ctx: TransformContext) -> str:
    """Build code evidence from snippet cache."""
    parts = []
    for citation, evidence in ctx.evidence_links.items():
        file_path, snippet = evidence
        if snippet:
            parts.append(f"{citation} - {file_path}")
            parts.append("```")
            parts.append(snippet[:300])  # First 300 chars
            parts.append("...")
            parts.append("```")
            parts.append("")
    return "\n".join(parts[:10])  # Limit to 10 citations


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

    max_refinements = cfg_obj.analysis.max_refinements if ctx.is_analysis else 0
    provider_used = "primary"

    if max_refinements <= 0:
        return anthropic_response, provider_used

    # Use grounding validation instead of quality scoring
    grounding_threshold = float(
        os.environ.get("GROUNDING_THRESHOLD", "0.8")
    ) if ctx.is_analysis else 1.0  # Disable for non-analysis

    logger.info(
        "[nonstream-grounding] Start: max_refinements=%d grounding_enabled=%s threshold=%.2f",
        max_refinements,
        cfg_obj.policy.grounding_validation_enabled if hasattr(cfg_obj, "policy") else False,
        grounding_threshold
    )

    # Grounding validation already run in response pipeline (server.py:345 or proxy.py:425)
    # Use ctx.grounding_score from response pipeline - DO NOT duplicate
    if ctx.is_analysis and cfg_obj.policy.grounding_validation_enabled:
        grounding_score = ctx.grounding_score if ctx.grounding_score > 0 else 1.0
    else:
        grounding_score = 1.0  # Disable if not in analysis

    logger.info(
        "[nonstream-grounding] Start: max_refinements=%d grounding_enabled=%s threshold=%.2f score=%.2f",
        max_refinements,
        cfg_obj.policy.grounding_validation_enabled if hasattr(cfg_obj, "policy") else False,
        grounding_threshold,
        grounding_score,
    )

    for attempt in range(1, max_refinements + 1):
        # Check grounding score (skip if not analysis or validation disabled)
        if not ctx.is_analysis or not cfg_obj.policy.grounding_validation_enabled:
            break

        if grounding_score >= grounding_threshold:
            break

        logger.info(
            "[refinement] attempt=%d/%d grounding_score=%.2f threshold=%.2f issues=%s",
            attempt, max_refinements, grounding_score, grounding_threshold, ctx.grounding_issues,
        )
        ctx.refinement_attempt = attempt

        feedback = _build_grounding_feedback(ctx, resp_text, grounding_threshold)

        # Add code snippets to feedback for verification
        if ctx.code_snippet_cache:
            code_evidence = _build_code_evidence(ctx)
            if code_evidence:
                feedback += "\n\nCODE EVIDENCE FROM TOOL RESULTS:\n" + code_evidence

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

        # Response pipeline runs on re-request - use ctx.grounding_score
        grounding_score = ctx.grounding_score if ctx.is_analysis else 1.0
        resp_text = extract_response_text(anthropic_response)

    if ctx.refinement_attempt > 0:
        logger.info(
            "[nonstream-grounding] Refined: attempts=%d final_score=%.2f issues=%s",
            ctx.refinement_attempt, ctx.grounding_score, ctx.grounding_issues,
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
        ctx.quality_score = 1.0  # tool-heavy = healthy work, not unscored
        ctx.quality_issues = []
        for chunk in chunks:
            yield chunk
        return

    # 1.55. Skip refinement when ANY tool calls are present — tool execution IS productive work.
    # Refining a tool-call response replaces the response before Claude Code can execute the tools,
    # breaking the tool result chain. Score the turn as passing to avoid LLM gate overhead.
    # EXCEPTION: BUILD/VERIFY intent — we score tool inputs for stub patterns (H18) even
    # when tool calls are present. The refinement fires on the NEXT response, not mid-chain.
    if tool_use_count > 0 and ctx.intent not in ("BUILD", "BUILDING", "VERIFY"):
        logger.info(
            "[stream-refinement] SKIP: tool_use_count=%d — tool execution in progress, no refinement",
            tool_use_count,
        )
        ctx.quality_score = 1.0
        ctx.quality_issues = []
        for chunk in chunks:
            yield chunk
        return

    # 1.6. Skip refinement if response contains CC workflow tools (EnterPlanMode, ExitPlanMode, etc.)
    # Re-requesting could drop these tool calls, breaking CC plan mode / workflow prompts.
    cc_workflow_hits = [n for n in tool_names if n in _CC_WORKFLOW_TOOLS]
    if cc_workflow_hits:
        logger.info(
            "[stream-refinement] SKIP: CC workflow tools %s — no refinement to preserve plan mode",
            cc_workflow_hits,
        )
        for chunk in chunks:
            yield chunk
        return

    # 2. Evaluate quality using unified scorer with real tool names
    real_tools = [{"type": "tool_use", "name": n} for n in tool_names]
    score, issues = score_response_quality(ctx.intent, text, real_tools, is_analysis=True)
    ctx.quality_score = score
    ctx.quality_issues = issues

    # 3. Two-tier gate: skip refinement if score is good or gate says PASS
    if max_refinements <= 0 or not await _should_refine(score, issues, ctx.intent, text, cfg_obj, ctx=ctx):
        _fire_quality_persist(ctx, issues)
        for chunk in chunks:
            yield chunk
        return

    # 4. Quality insufficient — refine via non-streaming re-requests
    # Lazy import: circular dependency (llm → proxy → llm)
    from proxy.proxy import run_messages

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
                                              request_messages=request.messages,
                                              intent=ctx.intent)
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


# ── Stream Response Pipeline (P1 — buffer + pipeline + quality gate) ──────────

def _build_unified_feedback(
    quality_score: float,
    quality_issues: list[str],
    ctx: "TransformContext",
    cfg: "ProxyConfig",
    request_messages: list | None,
    response_text: str,
) -> str:
    """Build combined quality + grounding feedback for a re-request."""
    parts = []

    # Quality feedback
    if quality_score < cfg.analysis.quality_threshold and quality_issues:
        parts.append(_build_refinement_feedback(
            quality_score, quality_issues, cfg.analysis.quality_threshold, request_messages,
            intent=ctx.intent,
        ))

    # Grounding feedback
    if (ctx.is_analysis
            and ctx.grounding_score < cfg.analysis.grounding_threshold
            and ctx.grounding_issues):
        parts.append(_build_grounding_feedback(ctx, response_text, cfg.analysis.grounding_threshold))

    return "\n\n".join(parts) if parts else "[quality-refinement] Improve response quality."


def _build_stream_envelope(
    text: str,
    tool_names: list[str],
    input_tokens: int,
    output_tokens: int,
    stop_reason: str,
    request: Any,
) -> Any:
    """Build a vendor-agnostic SimpleNamespace response envelope for stream buffering.

    Compatible with _ensure_request_object() in UniversalToolExtractionTransformer.
    The pipeline normalises any object to SimpleNamespace internally.
    """
    content = []
    if text.strip():
        content.append(SimpleNamespace(type="text", text=text))
    for i, name in enumerate(tool_names):
        content.append(SimpleNamespace(
            type="tool_use",
            id=f"toolu_stream_{i:03d}",
            name=name,
            input={},  # args not available from stream; sufficient for quality/grounding
        ))

    return SimpleNamespace(
        id="stream_synthetic",
        model=getattr(request, "model", ""),
        role="assistant",
        content=content,
        type="message",
        stop_reason=stop_reason,
        reasoning_content=None,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def _parse_stream_tokens(chunks: list[str]) -> tuple[int, int, str]:
    """Extract input_tokens, output_tokens, stop_reason from buffered SSE chunks."""
    input_tokens = 0
    output_tokens = 0
    stop_reason = "end_turn"
    for chunk in chunks:
        for line in chunk.split("\n"):
            if not line.startswith("data:"):
                continue
            data_str = line[5:].lstrip()
            if data_str == "[DONE]":
                continue
            try:
                data = json.loads(data_str)
            except (ValueError, json.JSONDecodeError):
                continue
            evt_type = data.get("type", "")
            if evt_type == "message_start":
                usage = data.get("message", {}).get("usage", {})
                input_tokens = usage.get("input_tokens", 0) or input_tokens
            elif evt_type == "message_delta":
                usage = data.get("usage", {})
                output_tokens = usage.get("output_tokens", 0) or output_tokens
                sr = data.get("delta", {}).get("stop_reason")
                if sr:
                    stop_reason = sr
    return input_tokens, output_tokens, stop_reason


async def stream_response_pipeline(
    stream_generator: Any,
    request: Any,
    ctx: "TransformContext",
    cfg: "ProxyConfig",
    response_pipeline: Any,
):
    """Buffer a streaming response, run the response pipeline, quality-gate, then replay or re-request.

    Equivalent to the non-streaming path for quality/grounding: the client waits,
    but receives a validated response.

    Yields SSE event strings compatible with StreamingResponse.
    """
    # STEP 1: Buffer the full stream (single async iteration)
    text, chunks, tool_names = await accumulate_stream(stream_generator)

    # Skip if tool-heavy (model is mid-execution — don't interrupt)
    if tool_names:
        logger.info(
            "[stream-pipeline] SKIP: tool_names=%s — tool execution in progress",
            tool_names[:3],
        )
        for chunk in chunks:
            yield chunk
        return

    # Skip quality gate during READ/ANALYZING phase (mid-analysis intermediate text).
    # In this phase the model produces brief planning thoughts ("I'll examine X next")
    # between tool-calling turns — those are NOT final analyses and score poorly by design.
    # Quality gate only makes sense on SYNTHESIZING (final write-up) or non-analysis intents.
    # EXCEPTION: never skip for empty responses (text == "") — those are upstream failures
    # (e.g. ConnectTimeout → LiteLLM fallback returning 0 tokens) and must be recovered.
    if ctx.is_analysis and ctx.analysis_phase in ("READ", "ANALYZING"):
        if text.strip():
            logger.info(
                "[stream-pipeline] SKIP: analysis_phase=%s — mid-analysis text, quality gate reserved for SYNTHESIZING",
                ctx.analysis_phase,
            )
            for chunk in chunks:
                yield chunk
            return
        logger.warning(
            "[stream-pipeline] EMPTY response in %s phase — bypassing READ skip to trigger re-request",
            ctx.analysis_phase,
        )

    # STEP 2: Parse token counts from buffered SSE events
    input_tokens, output_tokens, stop_reason = _parse_stream_tokens(chunks)
    ctx.stream_input_tokens = input_tokens
    ctx.stream_output_tokens = output_tokens
    ctx.stream_finish_reason = stop_reason

    # STEP 3: Build vendor-agnostic synthetic response
    synthetic = _build_stream_envelope(
        text, tool_names, input_tokens, output_tokens, stop_reason, request
    )

    # STEP 4: Run response pipeline (grounding + tool extraction + feedback)
    resp_ctx = TransformContext(
        intent=ctx.intent,
        is_analysis=ctx.is_analysis,
        phase=ctx.phase,
        analysis_phase=ctx.analysis_phase,
        tools=ctx.tools,
        session_id=ctx.session_id,
        plan_mode_active=ctx.plan_mode_active,
    )
    try:
        await response_pipeline.process(synthetic, resp_ctx)
    except Exception as e:
        logger.warning("[stream-pipeline] response pipeline failed: %s — replaying original", e)
        for chunk in chunks:
            yield chunk
        return

    # Propagate grounding results to main ctx
    ctx.grounding_score = resp_ctx.grounding_score
    ctx.grounding_issues = resp_ctx.grounding_issues
    ctx.evidence_links = resp_ctx.evidence_links
    ctx.evidence_graph = resp_ctx.evidence_graph

    # STEP 5: Quality gate
    real_tools = [{"type": "tool_use", "name": n} for n in tool_names]
    quality_score, issues = score_response_quality(
        ctx.intent, text, real_tools,
        is_analysis=ctx.is_analysis,
        input_tokens=input_tokens,
    )
    ctx.quality_score = quality_score
    ctx.quality_issues = issues

    needs_quality = await _should_refine(quality_score, issues, ctx.intent, text, cfg, ctx=ctx)
    needs_grounding = (
        ctx.is_analysis
        and cfg.analysis.grounding_refinement_enabled
        and ctx.grounding_score < cfg.analysis.grounding_threshold
        and len(resp_ctx.grounding_issues) > 0
    )

    # STEP 6a: PASS → replay original chunks
    if not needs_quality and not needs_grounding:
        _fire_quality_persist(ctx, issues)
        for chunk in chunks:
            yield chunk
        return

    logger.info(
        "[stream-pipeline] REFINE: quality=%.2f needs_quality=%s grounding=%.2f needs_grounding=%s",
        quality_score, needs_quality, ctx.grounding_score, needs_grounding,
    )

    # STEP 6b: FAIL → non-streaming re-request
    # Lazy import: circular dependency (llm → proxy → llm)
    from proxy.proxy import run_messages

    feedback = _build_unified_feedback(quality_score, issues, ctx, cfg, request.messages, text)

    if not hasattr(request, "messages") or request.messages is None:
        for chunk in chunks:
            yield chunk
        return

    request.messages.append(Message(role="assistant", content=text[:4000]))
    request.messages.append(Message(role="user", content=feedback))

    original_stream = getattr(request, "stream", True)
    request.stream = False  # force non-streaming for re-request

    try:
        is_stream, refined_out, _ = await run_messages(
            request_obj=request, cfg=cfg, ctx=ctx,
        )
        if is_stream or refined_out is None:
            for chunk in chunks:
                yield chunk
            return

        refined_anthropic = convert_litellm_to_anthropic(
            refined_out, request,
            model_context_window=ctx.effective_context_window or cfg.routing.model_context_window,
            strip_reasoning=cfg.policy.strip_reasoning,
        )
        ctx.refinement_attempt += 1
        _fire_quality_persist(ctx, ctx.quality_issues)

        model = getattr(request, "original_model", None) or request.model
        in_tokens = (refined_anthropic.usage.input_tokens
                     if refined_anthropic.usage else input_tokens)
        for event in response_to_sse_events(refined_anthropic, model, in_tokens):
            yield event

    except Exception as e:
        logger.warning("[stream-pipeline] re-request failed: %s — replaying original", e)
        for chunk in chunks:
            yield chunk
    finally:
        request.stream = original_stream


async def _build_verification_feedback(
    response_text: str,
    target_path: str,
) -> list[str]:
    """
    Build verification feedback by checking mentioned files against reality.

    Extracts file:line references from the response, checks if they exist,
    and provides specific feedback with alternatives if wrong.
    """
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
