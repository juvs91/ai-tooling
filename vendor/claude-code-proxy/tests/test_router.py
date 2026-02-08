# tests/test_router.py
"""Tests for router/llm_router.py and router/model_mapper.py."""
import pytest
from unittest.mock import MagicMock


class TestModelMapper:
    """Tests for model_mapper.py functions."""

    def test_claude_sonnet_maps_to_big_model(self):
        from router.model_mapper import map_claude_alias_to_target

        result = map_claude_alias_to_target(
            "claude-sonnet-4-20250514",
            preferred_provider="openai",
            big_model="gpt-4",
            small_model="gpt-3.5-turbo",
        )
        assert "gpt-4" in result

    def test_claude_haiku_maps_to_small_model(self):
        from router.model_mapper import map_claude_alias_to_target

        result = map_claude_alias_to_target(
            "claude-haiku-4-5-20251001",
            preferred_provider="openai",
            big_model="gpt-4",
            small_model="gpt-3.5-turbo",
        )
        assert "gpt-3.5-turbo" in result

    def test_adds_provider_prefix(self):
        from router.model_mapper import map_claude_alias_to_target

        result = map_claude_alias_to_target(
            "claude-sonnet-4-20250514",
            preferred_provider="openai",
            big_model="gpt-4",
            small_model="gpt-3.5-turbo",
        )
        assert result.startswith("openai/")

    def test_gemini_provider_prefix(self):
        from router.model_mapper import map_claude_alias_to_target

        result = map_claude_alias_to_target(
            "claude-sonnet-4-20250514",
            preferred_provider="google",
            big_model="gemini-pro",
            small_model="gemini-flash",
        )
        assert result.startswith("gemini/")


class TestLLMRouter:
    """Tests for llm_router.py functions."""

    def test_choose_local_model_defaults_to_small(self):
        from router.llm_router import choose_local_model

        result = choose_local_model(
            messages=[],
            max_out=100,
            approx_tokens=50,
            system_chars=100,
            tools_count=0,
            small_model="small",
            big_model="big",
            building_model="build",
        )
        assert result in ["small", "big", "build"]

    def test_choose_local_model_with_tools_prefers_building(self):
        from router.llm_router import choose_local_model

        result = choose_local_model(
            messages=[],
            max_out=1000,
            approx_tokens=1000,
            system_chars=5000,
            tools_count=10,  # Many tools suggests building/execution
            small_model="small",
            big_model="big",
            building_model="build",
        )
        # With many tools, should prefer building model
        assert result in ["build", "big"]

    def test_choose_local_model_planning_keywords(self):
        from router.llm_router import choose_local_model

        messages = [
            MagicMock(content="Please create a plan for implementing this feature")
        ]

        result = choose_local_model(
            messages=messages,
            max_out=1000,
            approx_tokens=500,
            system_chars=1000,
            tools_count=0,
            small_model="small",
            big_model="big",
            building_model="build",
        )
        # Planning keywords should influence model choice
        assert result in ["small", "big", "build"]

    def test_choose_local_model_building_keywords(self):
        from router.llm_router import choose_local_model

        messages = [
            MagicMock(content="Build the authentication module and write the code")
        ]

        result = choose_local_model(
            messages=messages,
            max_out=2000,
            approx_tokens=1000,
            system_chars=2000,
            tools_count=5,
            small_model="small",
            big_model="big",
            building_model="build",
        )
        # Building keywords should prefer building model
        assert result in ["build", "big"]


class TestProxyPolicy:
    """Tests for proxy.py policy functions."""

    def test_provider_cap_groq(self):
        from proxy.proxy import provider_cap_for_base_url

        cap = provider_cap_for_base_url("https://api.groq.com/openai/v1")
        assert cap == 5500

    def test_provider_cap_ollama(self):
        from proxy.proxy import provider_cap_for_base_url

        cap = provider_cap_for_base_url("http://localhost:11434/v1")
        assert cap == 25000

    def test_provider_cap_none(self):
        from proxy.proxy import provider_cap_for_base_url

        cap = provider_cap_for_base_url(None)
        assert cap == 0

    def test_provider_cap_unknown(self):
        from proxy.proxy import provider_cap_for_base_url

        cap = provider_cap_for_base_url("https://api.openai.com/v1")
        assert cap == 0

    def test_is_ollama_base_true(self):
        from proxy.proxy import is_ollama_base

        assert is_ollama_base("http://localhost:11434/v1") is True
        assert is_ollama_base("http://host.docker.internal:11434/v1") is True

    def test_is_ollama_base_false(self):
        from proxy.proxy import is_ollama_base

        assert is_ollama_base("https://api.openai.com/v1") is False
        assert is_ollama_base(None) is False
        assert is_ollama_base("") is False
