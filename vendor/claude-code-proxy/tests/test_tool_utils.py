# tests/test_tool_utils.py
"""Tests for utils/tool_utils.py model-detection predicates.

Covers is_fragile_orchestration_model (ADR-0037), added alongside the existing
is_no_tools_model to identify models documented as fragile at plan-mode /
deferred-tool orchestration reasoning, independent of native tool-calling
capability.
"""
import pytest

from utils.tool_utils import (
    _load_fragile_orchestration_models,
    is_fragile_orchestration_model,
)


@pytest.fixture(autouse=True)
def _clear_fragile_model_cache():
    """Reset the lru_cache(1) before and after every test so env changes take effect."""
    _load_fragile_orchestration_models.cache_clear()
    yield
    _load_fragile_orchestration_models.cache_clear()


class TestIsFragileOrchestrationModel:
    def test_default_matches_kimi(self, monkeypatch):
        """With no env var set, the default ('kimi') is active — the documented case."""
        monkeypatch.delenv("FRAGILE_ORCHESTRATION_MODELS", raising=False)
        assert is_fragile_orchestration_model("kimi-k2-instruct") is True
        assert is_fragile_orchestration_model("moonshotai/kimi-k2") is True

    def test_default_does_not_match_claude(self, monkeypatch):
        monkeypatch.delenv("FRAGILE_ORCHESTRATION_MODELS", raising=False)
        assert is_fragile_orchestration_model("claude-sonnet-5") is False

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.delenv("FRAGILE_ORCHESTRATION_MODELS", raising=False)
        assert is_fragile_orchestration_model("KIMI-K2") is True

    def test_env_var_csv_parsing(self, monkeypatch):
        monkeypatch.setenv("FRAGILE_ORCHESTRATION_MODELS", "kimi, glm-4.7 , deepseek-r1")
        assert is_fragile_orchestration_model("glm-4.7-air") is True
        assert is_fragile_orchestration_model("deepseek-r1-distill") is True
        assert is_fragile_orchestration_model("gpt-4o") is False

    def test_env_var_explicit_empty_disables(self, monkeypatch):
        """Explicitly setting the env var to empty disables the predicate entirely
        (unlike leaving it unset, which keeps the 'kimi' default)."""
        monkeypatch.setenv("FRAGILE_ORCHESTRATION_MODELS", "")
        assert is_fragile_orchestration_model("kimi-k2") is False

    def test_none_or_empty_model_returns_false(self, monkeypatch):
        monkeypatch.delenv("FRAGILE_ORCHESTRATION_MODELS", raising=False)
        assert is_fragile_orchestration_model(None) is False
        assert is_fragile_orchestration_model("") is False

    def test_short_patterns_filtered(self, monkeypatch):
        """Patterns of length <= 2 are dropped (mirrors is_no_tools_model's guard
        against accidental substring collisions from typos like a bare comma)."""
        monkeypatch.setenv("FRAGILE_ORCHESTRATION_MODELS", "kimi,ai")
        patterns = _load_fragile_orchestration_models()
        assert "ai" not in patterns
        assert "kimi" in patterns
