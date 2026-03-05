# tests/test_thinking_translation.py
"""Tests for thinking parameter handling in passthrough body builder.

The proxy scopes thinking injection to ANALYSIS_THINKING_PARAMS env var + ANALYZING/READ
phases only — thinking is NOT propagated from CC's request.thinking to other models since
they may not support it. Only the model configured in ANALYSIS_THINKING_PARAMS supports it.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

ANALYSIS_THINKING = {"thinking": {"type": "enabled"}}


def _make_request(thinking=None):
    """Build a minimal MessagesRequest-like namespace."""
    return SimpleNamespace(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "test"}],
        max_tokens=1000,
        system=None,
        tools=None,
        temperature=None,
        thinking=thinking,
    )


def _make_ctx(analysis_phase="PLANNING"):
    return SimpleNamespace(analysis_phase=analysis_phase)


def _build_body(request, model="glm-4.7", ctx=None, analysis_thinking=None):
    from proxy.proxy import _build_passthrough_body
    return _build_passthrough_body(request, model, ctx=ctx, analysis_thinking=analysis_thinking)


class TestThinkingInjection:

    def test_analyzing_phase_injects_thinking(self):
        """ANALYZING phase + env thinking_params → thinking in body."""
        req = _make_request()
        ctx = _make_ctx("ANALYZING")
        body = _build_body(req, ctx=ctx, analysis_thinking=ANALYSIS_THINKING)

        assert "thinking" in body
        assert body["thinking"] == {"type": "enabled"}

    def test_read_phase_injects_thinking(self):
        """READ phase + env thinking_params → thinking in body."""
        req = _make_request()
        ctx = _make_ctx("READ")
        body = _build_body(req, ctx=ctx, analysis_thinking=ANALYSIS_THINKING)

        assert "thinking" in body
        assert body["thinking"] == {"type": "enabled"}

    def test_planning_phase_no_thinking_even_with_env(self):
        """PLANNING phase + env thinking_params → thinking NOT injected (not a thinking phase)."""
        req = _make_request()
        ctx = _make_ctx("PLANNING")
        body = _build_body(req, ctx=ctx, analysis_thinking=ANALYSIS_THINKING)

        assert "thinking" not in body

    def test_building_phase_no_thinking(self):
        """BUILDING phase → thinking NOT injected."""
        req = _make_request()
        ctx = _make_ctx("BUILDING")
        body = _build_body(req, ctx=ctx, analysis_thinking=ANALYSIS_THINKING)

        assert "thinking" not in body

    def test_cc_thinking_param_does_not_trigger_injection_on_non_analysis_phase(self):
        """CC sends thinking=adaptive but phase is PLANNING → NOT injected.
        Thinking is scoped to ANALYSIS_THINKING_PARAMS model only."""
        req = _make_request(thinking={"type": "adaptive"})
        ctx = _make_ctx("PLANNING")
        body = _build_body(req, ctx=ctx, analysis_thinking=ANALYSIS_THINKING)

        assert "thinking" not in body, (
            "CC's thinking param must not propagate to non-ANALYZING phases — "
            "only the model configured in ANALYSIS_THINKING_PARAMS supports thinking"
        )

    def test_no_env_thinking_no_injection_even_on_analyzing(self):
        """ANALYZING phase but no ANALYSIS_THINKING_PARAMS configured → no thinking."""
        req = _make_request()
        ctx = _make_ctx("ANALYZING")
        body = _build_body(req, ctx=ctx, analysis_thinking=None)

        assert "thinking" not in body

    def test_env_thinking_value_passed_through_unchanged(self):
        """The exact value from ANALYSIS_THINKING_PARAMS is injected."""
        req = _make_request()
        ctx = _make_ctx("ANALYZING")
        custom_thinking = {"thinking": {"type": "enabled"}, "extra": "param"}
        body = _build_body(req, ctx=ctx, analysis_thinking=custom_thinking)

        assert body.get("thinking") == {"type": "enabled"}
        assert body.get("extra") == "param"

    def test_thinking_size_cap_respected(self, monkeypatch):
        """THINKING_MAX_INPUT_CHARS=10 with large body → thinking NOT injected."""
        monkeypatch.setenv("THINKING_MAX_INPUT_CHARS", "10")

        req = _make_request()
        req.messages = [{"role": "user", "content": "x" * 100}]
        ctx = _make_ctx("ANALYZING")
        body = _build_body(req, ctx=ctx, analysis_thinking=ANALYSIS_THINKING)

        assert "thinking" not in body, "Size cap should suppress thinking injection"

    def test_thinking_size_cap_zero_means_no_cap(self, monkeypatch):
        """THINKING_MAX_INPUT_CHARS=0 → no cap, thinking injected regardless of size."""
        monkeypatch.setenv("THINKING_MAX_INPUT_CHARS", "0")

        req = _make_request()
        req.messages = [{"role": "user", "content": "x" * 10000}]
        ctx = _make_ctx("ANALYZING")
        body = _build_body(req, ctx=ctx, analysis_thinking=ANALYSIS_THINKING)

        assert "thinking" in body, "Cap=0 means disabled, thinking should be injected"
