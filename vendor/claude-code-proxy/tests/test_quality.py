# tests/test_quality.py
"""Tests for analysis quality evaluation heuristics (Capa 3) + streaming quality loop."""
import pytest
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import after path setup
from llm.transformers.quality_refinement import (
    score_anthropic_response,
    analysis_quality_stream,
)
from llm.transformers.stream_event import accumulate_stream
from utils.quality import score_response


def _response(*text_blocks, tool_use_count=0):
    """Build a mock Anthropic response with text and optional tool_use blocks."""
    content = [{"type": "text", "text": t} for t in text_blocks]
    for _ in range(tool_use_count):
        content.append({"type": "tool_use", "name": "Read", "input": {"path": "f.py"}})
    return SimpleNamespace(content=content)


def _extract_text(*text_blocks):
    """Join text blocks as the server helper would."""
    return "\n".join(text_blocks)


def _make_tool_calls(count):
    """Build a list of Read tool call dicts."""
    return [{"type": "tool_use", "name": "Read", "input": {"file_path": f"f{i}.py"}} for i in range(count)]


class TestQualityEvaluation:

    def test_high_quality_response(self):
        """Long, specific, tool-using response scores high."""
        text = (
            "## Analysis of proxy.py (217 lines, 14 functions)\n"
            "The function `run_messages()` at line 142 handles the main execution.\n"
            "It calls `convert_anthropic_to_litellm()` which converts 13 field types.\n"
            "The retry logic in `_call_provider_with_retry()` uses exponential backoff.\n"
            "Token count: 4500 input, 1200 output across 8 test scenarios.\n"
        ) * 3  # Make it long enough
        tools = _make_tool_calls(5)
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert score >= 0.7
        assert "planning_too_short" not in issues

    def test_short_response_penalized(self):
        """Very short response gets penalized."""
        text = "The proxy handles requests."
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert score < 0.8
        assert "planning_too_short" in issues

    def test_no_tools_with_file_mentions_penalized(self):
        """Mentioning files without using tools is suspicious."""
        text = (
            "The file proxy.py handles routing.\n"
            "The file server.py starts FastAPI.\n"
            "The file config.py loads env vars.\n"
            "The file streaming.py handles SSE.\n"
            "No tools were used to verify any of this."
        )
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert "shallow_exploration" in issues

    def test_file_mentions_with_tools_ok(self):
        """Mentioning files WITH tool usage is fine."""
        text = (
            "After reading proxy.py, server.py, config.py, and streaming.py, "
            "I found 42 functions across 4 modules totaling 1800 lines."
        )
        tools = _make_tool_calls(4)
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert "shallow_exploration" not in issues

    def test_generic_phrases_penalized(self):
        """Overuse of generic phrases indicates superficial analysis."""
        text = (
            "The module handles request processing. "
            "It manages the connection pool. "
            "The router processes incoming data. "
            "The transformer deals with format conversion. "
            "The pipeline handles the transformation. "
            "The server manages HTTP endpoints. "
            "This component processes responses thoroughly."
        ) * 2
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert any("too_generic" in i for i in issues)

    def test_few_generic_phrases_ok(self):
        """A few generic phrases are fine."""
        text = (
            "The `run_messages()` function at line 142 handles the main execution loop. "
            "It converts Anthropic format to LiteLLM using `convert_anthropic_to_litellm()`, "
            "then applies 3 transformers: CompressionTransformer, ProviderQuirksTransformer, "
            "and CredentialTransformer. Retry logic uses exponential backoff with base delay 1.0s."
        ) * 3
        tools = _make_tool_calls(2)
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert not any("too_generic" in i for i in issues)

    def test_lacks_specificity_penalized(self):
        """Long text without concrete numbers is vague."""
        text = (
            "The proxy converts requests from one format to another. "
            "It supports multiple providers and can fall back between them. "
            "The configuration is loaded from environment variables. "
            "The streaming module converts chunks into events. "
        ) * 10  # Long but no numbers
        tools = _make_tool_calls(1)
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert "lacks_specificity" in issues

    def test_empty_response(self):
        """Empty response gets worst score."""
        score, issues = score_response("PLAN", "", [], is_analysis=True)
        assert score < 0.8
        assert "planning_too_short" in issues

    def test_perfect_score_achievable(self):
        """A comprehensive response can score 1.0."""
        text = (
            "## Detailed Analysis (verified via Read tool)\n\n"
            "### proxy.py — 217 lines, 6 functions\n"
            "I identified the following functions after reading proxy.py:\n"
            "1. `build_request_pipeline(cfg, models_differ)` — line 35\n"
            "2. `build_litellm_pipeline(cfg)` — line 46\n"
            "3. `_call_provider(request_obj, litellm_request)` — line 57\n"
            "4. `_is_retryable_check(exc)` — line 90\n"
            "5. `_call_provider_with_retry(...)` — line 107\n"
            "6. `run_messages(*, request_obj, cfg, ctx)` — line 142\n\n"
            "The pipeline executes 8 transformers in 2 phases.\n"
            "Phase 1 runs 5 transformers on the Anthropic-format request.\n"
            "Phase 2 runs 3 transformers on the LiteLLM-format request.\n"
            "Retry uses exponential backoff: delay = 1.0 * 2^attempt seconds.\n"
            "Maximum 5 retries before falling through to fallback chain.\n"
        )
        tools = _make_tool_calls(3)
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert score == 1.0
        assert issues == []

    def testscore_anthropic_response_helper(self):
        """The server helper correctly delegates to unified scorer."""
        text = (
            "## Analysis of proxy.py (217 lines, 14 functions)\n"
            "The function `run_messages()` at line 142 handles the main execution.\n"
            "Token count: 4500 input, 1200 output across 8 scenarios.\n"
        ) * 3
        resp = _response(text, tool_use_count=3)
        score, issues = score_anthropic_response(resp, "PLAN", is_analysis=True)
        assert score >= 0.7


# ── Streaming quality loop tests ──


def _sse_event(event_name, payload):
    """Build a single SSE event string."""
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


def _make_sse_stream(text, tool_use_count=0):
    """Build a list of SSE event strings simulating a streaming response."""
    events = []
    events.append(_sse_event("message_start", {
        "type": "message_start",
        "message": {"id": "msg_test", "type": "message", "role": "assistant",
                     "model": "test", "content": [], "stop_reason": None,
                     "usage": {"input_tokens": 10, "output_tokens": 0}},
    }))
    events.append(_sse_event("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    }))
    # Emit text in chunks
    chunk_size = 100
    for i in range(0, len(text), chunk_size):
        events.append(_sse_event("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text[i:i + chunk_size]},
        }))
    events.append(_sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}))
    # Add tool_use blocks
    for t in range(tool_use_count):
        events.append(_sse_event("content_block_start", {
            "type": "content_block_start", "index": t + 1,
            "content_block": {"type": "tool_use", "id": f"tool_{t}", "name": "Read", "input": {}},
        }))
        events.append(_sse_event("content_block_stop", {"type": "content_block_stop", "index": t + 1}))
    events.append(_sse_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 50},
    }))
    events.append(_sse_event("message_stop", {"type": "message_stop"}))
    events.append("data: [DONE]\n\n")
    return events


async def _async_gen(items):
    """Convert a list into an async generator."""
    for item in items:
        yield item


class TestEvaluateTextQuality:
    """Tests using score_response directly (replaces old _evaluate_text_quality)."""

    def test_good_text(self):
        text = (
            "## Analysis of proxy.py (217 lines, 14 functions)\n"
            "The function `run_messages()` at line 142 handles execution.\n"
            "Token count: 4500 input, 1200 output across 8 scenarios.\n"
        ) * 3
        tools = _make_tool_calls(5)
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert score >= 0.7
        assert "planning_too_short" not in issues

    def test_short_text(self):
        score, issues = score_response("PLAN", "Short.", [], is_analysis=True)
        assert score < 0.8
        assert "planning_too_short" in issues

    def test_no_tools_with_files(self):
        text = "proxy.py server.py config.py streaming.py mentioned without tools"
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert "shallow_exploration" in issues


class TestAccumulateStream:
    """Tests for accumulate_stream."""

    @pytest.mark.asyncio
    async def test_accumulates_text(self):
        events = _make_sse_stream("Hello world from the proxy analysis!")
        text, chunks, tool_names = await accumulate_stream(_async_gen(events))
        assert "Hello world" in text
        assert tool_names == []
        assert len(chunks) == len(events)

    @pytest.mark.asyncio
    async def test_extracts_tool_names(self):
        events = _make_sse_stream("Analysis text", tool_use_count=3)
        text, chunks, tool_names = await accumulate_stream(_async_gen(events))
        assert len(tool_names) == 3
        assert all(n == "Read" for n in tool_names)
        assert "Analysis text" in text

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        text, chunks, tool_names = await accumulate_stream(_async_gen([]))
        assert text == ""
        assert chunks == []
        assert tool_names == []


class TestAnalysisQualityStream:
    """Tests for analysis_quality_stream."""

    def _make_cfg(self, max_refinements=2, threshold=0.75):
        return SimpleNamespace(
            analysis=SimpleNamespace(
                max_refinements=max_refinements,
                quality_threshold=threshold,
                score_certainty_floor=0.50,
                llm_score_gate=False,
            ),
            routing=SimpleNamespace(model_context_window=200000),
            classifier=SimpleNamespace(model="", api_key="", base_url=None),
            policy=SimpleNamespace(strip_reasoning=False),
        )

    def _make_request(self):
        from llm.schemas import Message
        return SimpleNamespace(
            model="test-model",
            original_model="claude-opus-4-6",
            stream=True,
            messages=[Message(role="user", content="Analyze the codebase")],
            max_tokens=8192,
        )

    def _make_ctx(self):
        from llm.pipeline import TransformContext
        return TransformContext(raw_body=b"", is_analysis=True, intent="PLAN")

    @pytest.mark.asyncio
    async def test_good_quality_replays_original(self):
        """Score >= threshold: original chunks are replayed."""
        good_text = (
            "## Analysis of proxy.py (217 lines, 14 functions)\n"
            "The function `run_messages()` at line 142 handles execution.\n"
            "Token count: 4500 input, 1200 output across 8 scenarios.\n"
        ) * 3
        events = _make_sse_stream(good_text, tool_use_count=3)
        cfg = self._make_cfg(max_refinements=2, threshold=0.75)
        request = self._make_request()
        ctx = self._make_ctx()

        result = []
        async for chunk in analysis_quality_stream(
            _async_gen(events), request, ctx, cfg,
        ):
            result.append(chunk)

        # Should replay all original chunks
        assert len(result) == len(events)
        assert result == events

    @pytest.mark.asyncio
    async def test_bad_quality_triggers_refinement(self):
        """Score < threshold: triggers re-request via run_messages."""
        bad_text = "Short bad analysis."
        events = _make_sse_stream(bad_text)
        cfg = self._make_cfg(max_refinements=1, threshold=0.75)
        request = self._make_request()
        ctx = self._make_ctx()

        # Mock run_messages to return a non-streaming good response
        good_response = SimpleNamespace(
            content=[SimpleNamespace(
                type="text",
                text=(
                    "## Detailed proxy.py analysis — 217 lines, 14 functions\n"
                    "run_messages() at line 142 converts Anthropic to LiteLLM.\n"
                    "Token count: 4500 input, 1200 output. 8 transformers total.\n"
                ) * 3,
            )],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=100, output_tokens=500),
        )

        with patch("proxy.proxy.run_messages", new_callable=AsyncMock) as mock_run, \
             patch("llm.transformers.quality_refinement.convert_litellm_to_anthropic", return_value=good_response):
            mock_run.return_value = (False, MagicMock(), "primary")

            result = []
            async for chunk in analysis_quality_stream(
                _async_gen(events), request, ctx, cfg,
            ):
                result.append(chunk)

            # Should have called run_messages for refinement
            mock_run.assert_called_once()
            # Result should contain SSE events from response_to_sse_events
            combined = "".join(result)
            assert "message_start" in combined
            assert "message_stop" in combined

    @pytest.mark.asyncio
    async def test_no_refinements_replays(self):
        """max_refinements=0: always replays original."""
        bad_text = "Short."
        events = _make_sse_stream(bad_text)
        cfg = self._make_cfg(max_refinements=0, threshold=0.75)
        request = self._make_request()
        ctx = self._make_ctx()

        result = []
        async for chunk in analysis_quality_stream(
            _async_gen(events), request, ctx, cfg,
        ):
            result.append(chunk)

        assert result == events

    @pytest.mark.asyncio
    async def test_tool_heavy_response_skips_refinement(self):
        """Tool-heavy response with short text: replayed without scoring (Fix 6)."""
        short_text = "Let me read the files."
        events = _make_sse_stream(short_text, tool_use_count=3)
        cfg = self._make_cfg(max_refinements=2, threshold=0.75)
        request = self._make_request()
        ctx = self._make_ctx()

        result = []
        async for chunk in analysis_quality_stream(
            _async_gen(events), request, ctx, cfg,
        ):
            result.append(chunk)

        # Should replay all original chunks (skip refinement for tool-heavy)
        assert len(result) == len(events)
        assert result == events


# ── Refinement type detection (Item 3) ───────────────────────────────────────

from llm.transformers.quality_refinement import _build_refinement_feedback


class TestRefinementTypeDetection:
    """_build_refinement_feedback must prefix the output with [quality-refinement:{type}]
    and generate targeted messages per heuristic type."""

    def test_stub_type_detected_and_prefix_present(self):
        issues = ["stub_implementations(2_stubs)"]
        result = _build_refinement_feedback(0.4, issues, 0.7, intent="BUILD")
        assert "[quality-refinement:stub]" in result

    def test_stub_type_with_stubbed_functions_issue(self):
        issues = ["stubbed_functions(foo,bar)"]
        result = _build_refinement_feedback(0.4, issues, 0.7, intent="BUILD")
        assert "[quality-refinement:stub]" in result

    def test_unverified_claims_type_detected(self):
        issues = ["unverified_claims(5)"]
        result = _build_refinement_feedback(0.5, issues, 0.7)
        assert "[quality-refinement:unverified_claims]" in result

    def test_unverified_claims_count_in_message(self):
        issues = ["unverified_claims(3)"]
        result = _build_refinement_feedback(0.5, issues, 0.7)
        assert "3 factual claim(s)" in result

    def test_unverified_claims_fallback_without_count(self):
        """unverified issue without count pattern still generates targeted message."""
        issues = ["unverified_something"]
        result = _build_refinement_feedback(0.5, issues, 0.7)
        assert "[quality-refinement:unverified_claims]" in result

    def test_shallow_exploration_type_detected(self):
        issues = ["shallow_exploration(mentioned=5,read=2)"]
        result = _build_refinement_feedback(0.55, issues, 0.7)
        assert "[quality-refinement:shallow_exploration]" in result

    def test_shallow_exploration_counts_in_message(self):
        issues = ["shallow_exploration(mentioned=7,read=3)"]
        result = _build_refinement_feedback(0.55, issues, 0.7)
        assert "mentioned 7 files" in result
        assert "only read 3" in result

    def test_shallow_exploration_fallback_without_counts(self):
        """shallow issue without counts still generates targeted message."""
        issues = ["shallow_response"]
        result = _build_refinement_feedback(0.55, issues, 0.7)
        assert "[quality-refinement:shallow_exploration]" in result

    def test_grounding_type_detected(self):
        issues = ["grounding_score_low(0.45)"]
        result = _build_refinement_feedback(0.55, issues, 0.7)
        assert "[quality-refinement:grounding]" in result

    def test_specificity_type_detected(self):
        issues = ["specificity_low"]
        result = _build_refinement_feedback(0.55, issues, 0.7)
        assert "[quality-refinement:specificity]" in result

    def test_generic_type_fallback(self):
        issues = ["some_unknown_heuristic_xyz"]
        result = _build_refinement_feedback(0.4, issues, 0.7)
        assert "[quality-refinement:generic]" in result

    def test_first_match_wins_stub_before_unverified(self):
        """When multiple issues present, first-match wins (stub before unverified)."""
        issues = ["stub_implementations(1_stubs)", "unverified_claims(2)"]
        result = _build_refinement_feedback(0.4, issues, 0.7, intent="BUILD")
        assert "[quality-refinement:stub]" in result
        assert "[quality-refinement:unverified_claims]" not in result

    def test_score_and_threshold_in_prefix(self):
        issues = ["stub_implementations(1_stubs)"]
        result = _build_refinement_feedback(0.45, issues, 0.70, intent="BUILD")
        assert "45%" in result or "0.45" in result or "Score:" in result
        assert "70%" in result or "0.70" in result or "Threshold:" in result
