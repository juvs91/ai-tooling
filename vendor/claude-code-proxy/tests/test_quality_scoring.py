# tests/test_quality_scoring.py
"""Tests for utils/quality.py — response quality heuristics."""
from utils.quality import score_response


class TestScoreResponse:
    def test_perfect_chat_response(self):
        score, issues = score_response("CHAT", "The answer is 42.")
        assert score == 1.0
        assert issues == []

    def test_perfect_planning_response(self):
        text = "Here is the plan:\n" + "Step details. " * 50  # long enough
        score, issues = score_response("PLAN", text)
        assert score == 1.0
        assert issues == []

    def test_planning_too_short(self):
        score, issues = score_response("PLAN", "Do this.", [])
        assert "planning_too_short" in issues
        assert score < 1.0

    def test_planning_short_with_tools_ok(self):
        # Short text is fine if there are tool calls (model is taking action)
        score, issues = score_response(
            "PLAN", "Let me plan.",
            [{"type": "tool_use", "name": "Read", "input": {"file_path": "/foo"}}],
        )
        assert "planning_too_short" not in issues

    def test_chat_too_verbose(self):
        text = "word " * 2000  # ~10000 chars
        score, issues = score_response("CHAT", text)
        assert "chat_too_verbose" in issues

    def test_reasoning_leak(self):
        text = "<reasoning>\nThe user wants...\n</reasoning>\n\nHere is the answer."
        score, issues = score_response("CHAT", text)
        assert "reasoning_leak" in issues
        assert score == 0.8

    def test_empty_response(self):
        score, issues = score_response("CHAT", "", [])
        assert "empty_response" in issues
        assert score <= 0.5

    def test_invalid_tool_json(self):
        tool_calls = [{"input": "{invalid json"}]
        score, issues = score_response("BUILD", "Using the tool.", tool_calls)
        assert "invalid_tool_json" in issues

    def test_valid_tool_json(self):
        tool_calls = [{"input": '{"file_path": "/etc/hostname"}'}]
        score, issues = score_response("BUILD", "Reading file.", tool_calls)
        assert "invalid_tool_json" not in issues

    def test_dict_input_not_string(self):
        # input is already a dict (not a string) — should not trigger json check
        tool_calls = [{"input": {"file_path": "/etc/hostname"}}]
        score, issues = score_response("BUILD", "Reading file.", tool_calls)
        assert "invalid_tool_json" not in issues

    def test_truncated_code_block(self):
        text = "Here is the code:\n```python\ndef foo():\n    pass"
        # Odd number of ``` and ends with code-like content
        score, issues = score_response("BUILD", text)
        assert "truncated_code_block" in issues

    def test_complete_code_block(self):
        text = "Here:\n```python\ndef foo():\n    pass\n```"
        score, issues = score_response("BUILD", text)
        assert "truncated_code_block" not in issues

    def test_score_never_negative(self):
        # Stack multiple penalties
        text = ""  # empty + no tools
        score, issues = score_response("PLAN", text, [{"input": "{bad"}])
        assert score >= 0.0

    def test_none_tool_calls(self):
        score, issues = score_response("CHAT", "Hello.", None)
        assert score == 1.0


class TestAnalysisHeuristics:
    """Tests for analysis-specific heuristics H6-H9 (is_analysis=True)."""

    def test_shallow_exploration_no_tools(self):
        """Mentions >3 files but no Read/Glob/Grep tools → penalizes."""
        text = "Found issues in server.py, config.py, streaming.py, converters.py and quality.py."
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert "shallow_exploration" in issues
        assert score < 1.0

    def test_shallow_exploration_ok_with_read_tools(self):
        """Same text but with enough Read tools → no penalty."""
        text = "Found issues in server.py, config.py, streaming.py, converters.py."
        tools = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "server.py"}},
            {"type": "tool_use", "name": "Grep", "input": {"pattern": "def main"}},
        ]
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert "shallow_exploration" not in issues

    def test_shallow_exploration_low_ratio(self):
        """Many file mentions but very few reads → penalizes."""
        text = (
            "Analyzed server.py, config.py, streaming.py, converters.py, "
            "pipeline.py, compressor.py, schemas.py, sse.py, quality.py, metrics.py."
        )
        tools = [{"type": "tool_use", "name": "Read", "input": {"file_path": "server.py"}}]
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert "shallow_exploration" in issues

    def test_shallow_exploration_not_triggered_without_analysis(self):
        """Non-analysis requests don't trigger H6."""
        text = "Found issues in server.py, config.py, streaming.py, converters.py."
        score, issues = score_response("PLAN", text, [], is_analysis=False)
        assert "shallow_exploration" not in issues

    def test_unverified_claims(self):
        """Makes >5 factual claims about code without using any tools."""
        text = (
            "Here is my complete analysis of the codebase. "
            "The function parse_args takes 3 parameters. "
            "The module has 15 classes to manage state. "
            "Config loads from 2 different sources. "
            "The pipeline runs 5 transformers in sequence. "
            "Router maps 3 intents to different models. "
            "Server handles 8 endpoints for the API. "
            "Quality runs 10 heuristics for scoring. "
        ) * 3  # Make it long enough to avoid planning_too_short
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert "unverified_claims" in issues
        assert score < 1.0

    def test_unverified_claims_ok_with_tools(self):
        """Same claims but with tool usage → no penalty."""
        text = (
            "Here is my complete analysis of the codebase. "
            "The function parse_args takes 3 parameters. "
            "The module has 15 classes to manage state. "
            "Config loads from 2 different sources. "
            "The pipeline runs 5 transformers in sequence. "
            "Router maps 3 intents to different models. "
            "Server handles 8 endpoints for the API. "
            "Quality runs 10 heuristics for scoring. "
        ) * 3
        tools = [{"type": "tool_use", "name": "Read", "input": {"file_path": "server.py"}}]
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert "unverified_claims" not in issues

    def test_too_generic_analysis(self):
        """Analysis with too many generic phrases (>3)."""
        text = (
            "The module handles authentication. It manages user sessions. "
            "This processes incoming requests. It deals with errors gracefully."
        )
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert any("too_generic" in i for i in issues)
        assert score < 1.0

    def test_too_generic_few_matches_ok(self):
        """2 generic phrases is fine — only >3 triggers."""
        text = "The module handles auth. It manages sessions. All clear."
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert not any("too_generic" in i for i in issues)

    def test_lacks_specificity(self):
        """Long analysis text (>1000 chars) without concrete numbers (<5)."""
        text = "This module is responsible for processing data and transforming it. " * 30
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert "lacks_specificity" in issues

    def test_lacks_specificity_ok_with_numbers(self):
        """Long text with enough numbers → no penalty."""
        text = (
            "The module has 150 lines across 12 functions. "
            "It processes requests in 45ms average. " * 20
        )
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert "lacks_specificity" not in issues

    def test_good_analysis_passes_all(self):
        """A good analysis with tools, numbers, and specificity → high score."""
        text = (
            "server.py has 679 lines. The create_message handler at line 402 "
            "processes requests through a 5-step pipeline. convert_litellm_to_anthropic "
            "at line 526 handles 12 edge cases including finish_reason='length'. "
            "The quality scorer runs 10 heuristics."
        )
        tools = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "server.py"}},
            {"type": "tool_use", "name": "Grep", "input": {"pattern": "def create_message"}},
        ]
        score, issues = score_response("PLAN", text, tools, is_analysis=True)
        assert score >= 0.7
        assert "shallow_exploration" not in issues
        assert "unverified_claims" not in issues

    def test_analysis_planning_too_short_stricter(self):
        """Analysis requests use 500 char threshold (not 200)."""
        text = "Here is a short plan. " * 10  # ~220 chars, passes normal but fails analysis
        score, issues = score_response("PLAN", text, [], is_analysis=True)
        assert "planning_too_short" in issues

    def test_stacked_penalties_floor_at_zero(self):
        """Multiple analysis penalties still floor at 0.0."""
        # empty text + no tools → empty_response (-0.5) + planning_too_short (-0.3) = 0.2
        score, issues = score_response("PLAN", "", [], is_analysis=True)
        assert score <= 0.2
        assert "empty_response" in issues
        assert "planning_too_short" in issues

    def test_stacked_penalties_with_bad_json(self):
        """Empty text + invalid tool JSON → multiple penalties."""
        score, issues = score_response("PLAN", "", [{"input": "{bad"}], is_analysis=True)
        assert score >= 0.0
        assert "invalid_tool_json" in issues

    def test_h11_analysis_too_shallow(self):
        """Very short text + <2 tools on analysis → penalizes."""
        score, issues = score_response("BUILD", "I'll look at the code.", [], is_analysis=True)
        assert "analysis_too_shallow" in issues
        assert score <= 0.6

    def test_h11_not_triggered_with_enough_tools(self):
        """Short text but >=2 tools → H11 does not fire."""
        tools = [
            {"type": "tool_use", "name": "Read"},
            {"type": "tool_use", "name": "Grep"},
        ]
        score, issues = score_response("BUILD", "Reading files.", tools, is_analysis=True)
        assert "analysis_too_shallow" not in issues

    def test_h11_not_triggered_without_analysis(self):
        """Non-analysis short responses are fine."""
        score, issues = score_response("BUILD", "Done.", [])
        assert "analysis_too_shallow" not in issues

    def test_h12_removed_tools_with_short_text_ok(self):
        """H12 removed: short text + tool calls is normal CC behavior (no penalty)."""
        tools = [{"type": "tool_use", "name": "Read"} for _ in range(5)]
        score, issues = score_response("BUILD", "Let me read the files.", tools, is_analysis=True)
        assert "analysis_all_tools_no_substance" not in issues
        assert "analysis_too_shallow" not in issues  # H11 also doesn't fire (has tools)

    def test_h12_not_triggered_with_enough_text(self):
        """>=200 chars of text + tools → H12 does not fire."""
        text = "Here is my detailed analysis of the system. " * 10
        tools = [{"type": "tool_use", "name": "Read"}]
        score, issues = score_response("BUILD", text, tools, is_analysis=True)
        assert "analysis_all_tools_no_substance" not in issues

    def test_h1_applies_to_analysis_building(self):
        """H1 now fires for is_analysis=True even with intent=BUILDING."""
        score, issues = score_response("BUILD", "Short.", [], is_analysis=True)
        assert "planning_too_short" in issues

    def test_tool_response_no_penalties(self):
        """Short text + tools = normal CC behavior, no H11/H12 penalties."""
        tools = [{"type": "tool_use", "name": "Read"}]
        score, issues = score_response("BUILD", "Ok.", tools, is_analysis=True)
        assert "analysis_too_shallow" not in issues  # H11: has tools (len != 0)
        assert "analysis_all_tools_no_substance" not in issues  # H12: removed


class TestContextInclusiveBypass:
    """Tests for context_inclusive bypass of H6/H7 (input_tokens > 15000)."""

    def test_h6_skipped_when_context_inclusive(self):
        """H6 (shallow_exploration) must NOT fire when input_tokens > 15000.

        Passthrough analysis already has files in context — no tools during response is correct.
        """
        text = (
            "The analysis of server.py, config.py, streaming.py, converters.py, "
            "pipeline.py, compressor.py shows the following patterns: "
            "each module has 200-500 lines and handles a specific concern. "
            "server.py:150 is the main entry point. pipeline.py:80 wires transformers."
        )
        score, issues = score_response(
            "READ", text, [],
            is_analysis=True,
            input_tokens=20000,  # large context → context_inclusive
        )
        assert "shallow_exploration" not in issues, (
            "H6 must be skipped when input_tokens > 15000 (passthrough has files in context)"
        )

    def test_h7_skipped_when_context_inclusive(self):
        """H7 (unverified_claims) must NOT fire when input_tokens > 15000."""
        text = (
            "The function parse_args takes 3 parameters. "
            "The module has 15 classes to manage state. "
            "Config loads from 2 different sources. "
            "The pipeline runs 5 transformers in sequence. "
            "Router maps 3 intents to different models. "
            "Server handles 8 endpoints for the API. "
            "Quality runs 10 heuristics for scoring. "
        ) * 3
        score, issues = score_response(
            "READ", text, [],
            is_analysis=True,
            input_tokens=30000,  # large context → context_inclusive
        )
        assert "unverified_claims" not in issues, (
            "H7 must be skipped when input_tokens > 15000 (claims backed by context data)"
        )

    def test_h6_fires_without_context_inclusive(self):
        """H6 still fires for normal analysis (no large context) without tools."""
        text = "Found issues in server.py, config.py, streaming.py, converters.py and quality.py."
        score, issues = score_response(
            "PLAN", text, [],
            is_analysis=True,
            input_tokens=0,  # not context_inclusive
        )
        assert "shallow_exploration" in issues

    def test_h7_fires_without_context_inclusive(self):
        """H7 still fires for normal analysis (no large context) without tools."""
        text = (
            "The function parse_args takes 3 parameters. "
            "The module has 15 classes to manage state. "
            "Config loads from 2 different sources. "
            "The pipeline runs 5 transformers in sequence. "
            "Router maps 3 intents to different models. "
            "Server handles 8 endpoints for the API. "
            "Quality runs 10 heuristics for scoring. "
        ) * 3
        score, issues = score_response(
            "PLAN", text, [],
            is_analysis=True,
            input_tokens=5000,  # not context_inclusive
        )
        assert "unverified_claims" in issues


class TestH11bShallowOutputRatio:
    """Tests for H11b — shallow output ratio for large-context analysis."""

    def test_h11b_critically_shallow_output(self):
        """Large context + very short output (<33% of expected) → critically_shallow_output."""
        short_text = "The analysis shows some issues."  # ~31 chars
        score, issues = score_response(
            "READ", short_text, [],
            is_analysis=True,
            input_tokens=60000,  # expected_min = min(3000, 60000//20) = 3000; 33% = 1000
        )
        assert "critically_shallow_output" in issues
        assert score < 0.7

    def test_h11b_shallow_output(self):
        """Large context + output between 33-67% of expected → shallow_output."""
        # input_tokens=60000 → expected_min=3000; 67%=2000; 33%=1000
        # Need: 1000 < text_len < 2000
        medium_text = "Analysis summary. " * 80  # ~1440 chars (between 1000 and 2000)
        score, issues = score_response(
            "READ", medium_text, [],
            is_analysis=True,
            input_tokens=60000,
        )
        assert "shallow_output" in issues
        assert "critically_shallow_output" not in issues

    def test_h11b_adequate_output_no_penalty(self):
        """Large context + adequate output (>67% of expected) → no H11b penalty."""
        # input_tokens=60000 → expected_min=3000; 67%=2000 — need text_len >= 2000
        adequate_text = "Detailed analysis of the module. " * 70  # ~2310 chars
        score, issues = score_response(
            "READ", adequate_text, [],
            is_analysis=True,
            input_tokens=60000,
        )
        assert "shallow_output" not in issues
        assert "critically_shallow_output" not in issues

    def test_h11b_not_triggered_for_small_context(self):
        """H11b only triggers when input_tokens > 15000."""
        short_text = "The analysis shows some issues."
        score, issues = score_response(
            "READ", short_text, [],
            is_analysis=True,
            input_tokens=5000,  # below threshold
        )
        assert "shallow_output" not in issues
        assert "critically_shallow_output" not in issues

    def test_h11b_cap_at_3000_expected_min(self):
        """expected_min caps at 3000 chars regardless of input_tokens size."""
        # input_tokens=1000000 → min(3000, 1000000//20=50000) = 3000
        # Short text well below 33% of 3000 (= 1000)
        short_text = "Brief."
        score, issues = score_response(
            "READ", short_text, [],
            is_analysis=True,
            input_tokens=1000000,
        )
        assert "critically_shallow_output" in issues


class TestHSpecificity:
    """Tests for H_SPECIFICITY — code-level references distinguish deep analysis."""

    def test_lacks_code_specificity(self):
        """Analysis >500 chars with 0 code-level references → lacks_code_specificity."""
        # Generic analysis with no line numbers, function calls, or file paths — all vague prose
        text = (
            "The system uses a pipeline architecture. Each component handles "
            "a specific concern and passes data to the next stage. "
            "Error handling is centralized in the middleware layer. "
            "Configuration is loaded at startup and cached for reuse. "
            "The routing logic determines which model to use based on intent. "
            "Quality scoring evaluates responses against multiple criteria. "
            "Metrics are tracked per request and aggregated for reporting. "
            "The design is modular so teams can extend it without coupling. "
            "Each layer has its own responsibility and interface contract. "
            "The fallback chain ensures resilience when providers are unavailable. "
        )  # >500 chars, no code refs (no .py files, no line N, no func())
        score, issues = score_response(
            "READ", text, [],
            is_analysis=True,
        )
        assert "lacks_code_specificity" in issues
        assert score <= 0.8

    def test_low_code_specificity(self):
        """Analysis with few code refs (specificity 5-9) → low_code_specificity."""
        # 2 line refs (line 150, line 220) + 1 file ref (server.py) = 2*2 + 0 + 1 = 5
        # >=5 but <10 → low_code_specificity (not lacks_code_specificity)
        text = (
            "The server.py module handles routing for all incoming requests. "
            "At line 150, the main handler dispatches to the correct pipeline. "
            "The error recovery logic at line 220 handles provider timeouts gracefully. "
            "The pipeline runs 5 stages in sequence passing context between them. "
            "Each stage reads config and produces output for the next stage. "
            "The quality module evaluates responses and records scores in the tracker. "
            "The system is designed to be modular and extensible over time. "
            "Additional providers can be added without modifying the core logic. "
        )
        # line refs: "line 150", "line 220" → 2 matches × 2pts = 4pts
        # file refs: "server.py" → 1 match × 1pt = 1pt
        # func refs: 0
        # specificity_score = 4 + 0 + 1 = 5 → not < 5, but < 10 → low_code_specificity
        score, issues = score_response(
            "READ", text, [],
            is_analysis=True,
        )
        assert "low_code_specificity" in issues
        assert "lacks_code_specificity" not in issues

    def test_good_code_specificity_no_penalty(self):
        """Analysis with rich code references (specificity >= 10) → no penalty."""
        text = (
            "server.py has 679 lines. The create_message() handler at line 402 "
            "dispatches to handle_streaming() or _passthrough(). "
            "convert_litellm_to_anthropic() at line 526 handles 12 edge cases "
            "including finish_reason='length'. The class TransformContext "
            "at pipeline.py:45 carries intent and quality_score across stages. "
            "score_response() in quality.py applies 16 heuristics. "
            "def _regex_fallback_intent() at router/llm_router.py:80 matches patterns. "
        )
        # Line refs: "line 402", "line 526", "pipeline.py:45" → 3 × 2 = 6
        # Func refs: create_message(), handle_streaming(), convert_litellm_to_anthropic(),
        #            score_response() → 4; class TransformContext → 1; def _regex_fallback_intent() → 1 = 6
        # File refs: server.py, pipeline.py (covered above), quality.py, llm_router.py → 4
        # specificity_score = 6 + 6 + 4 = 16 → not < 10 → no penalty
        score, issues = score_response(
            "READ", text, [],
            is_analysis=True,
        )
        assert "lacks_code_specificity" not in issues
        assert "low_code_specificity" not in issues

    def test_h_specificity_not_triggered_for_short_text(self):
        """H_SPECIFICITY only fires when text_len > 500."""
        short_text = "Brief analysis."  # < 500 chars
        score, issues = score_response(
            "READ", short_text, [],
            is_analysis=True,
        )
        assert "lacks_code_specificity" not in issues
        assert "low_code_specificity" not in issues

    def test_h_specificity_not_triggered_without_analysis(self):
        """H_SPECIFICITY only fires for is_analysis=True."""
        # Long text, non-analysis intent
        text = (
            "General response about the system. "
            "No specific code references needed. "
        ) * 20  # > 500 chars
        score, issues = score_response(
            "CHAT", text, [],
            is_analysis=False,
        )
        assert "lacks_code_specificity" not in issues


class TestModelCosts:
    """Tests for config.ModelCosts."""

    def test_cost_calculation(self):
        from config import ModelCosts
        mc = ModelCosts(rates={"glm-4.7": (0.38, 0.38)})
        cost = mc.cost_usd("glm-4.7", 1000, 500)
        expected = (1000 * 0.38 + 500 * 0.38) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_cost_strips_provider_prefix(self):
        from config import ModelCosts
        mc = ModelCosts(rates={"glm-4.7": (0.38, 0.38)})
        cost = mc.cost_usd("openai/glm-4.7", 1000, 500)
        assert cost > 0

    def test_cost_unknown_model_returns_zero(self):
        from config import ModelCosts
        mc = ModelCosts(rates={"glm-4.7": (0.38, 0.38)})
        cost = mc.cost_usd("unknown-model", 1000, 500)
        assert cost == 0.0

    def test_parse_model_costs(self):
        import os
        os.environ["MODEL_COSTS"] = "glm-4.7:0.38:0.38,deepseek-chat:0.001:0.002"
        from config import _parse_model_costs
        mc = _parse_model_costs()
        assert mc.rates["glm-4.7"] == (0.38, 0.38)
        assert mc.rates["deepseek-chat"] == (0.001, 0.002)
        del os.environ["MODEL_COSTS"]

    def test_parse_model_costs_empty(self):
        import os
        os.environ.pop("MODEL_COSTS", None)
        from config import _parse_model_costs
        mc = _parse_model_costs()
        assert mc.rates == {}
