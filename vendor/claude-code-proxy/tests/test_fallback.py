# tests/test_fallback.py
"""Tests for ProviderConfig and fallback provider chain."""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.schemas import ProviderConfig


# ── ProviderConfig.get_litellm_model ─────────────────────────────────


class TestProviderConfigGetLitellmModel:
    @pytest.fixture
    def provider(self):
        return ProviderConfig(
            name="test",
            provider_prefix="openai",
            api_key="key",
            big_model="glm-4.7",
            small_model="glm-4.7-flash",
            building_model="glm-4.7",
        )

    def test_chat_uses_small_model(self, provider):
        assert provider.get_litellm_model("CHAT") == "openai/glm-4.7-flash"

    def test_planning_uses_big_model(self, provider):
        assert provider.get_litellm_model("PLANNING") == "openai/glm-4.7"

    def test_building_uses_building_model(self, provider):
        # building_model == big_model, so stays on big
        assert provider.get_litellm_model("BUILDING") == "openai/glm-4.7"

    def test_building_uses_separate_model_when_different(self):
        p = ProviderConfig(
            name="test", provider_prefix="openai", api_key="key",
            big_model="big", small_model="small", building_model="builder",
        )
        assert p.get_litellm_model("BUILDING") == "openai/builder"

    def test_chat_same_models_uses_big(self):
        p = ProviderConfig(
            name="test", provider_prefix="openai", api_key="key",
            big_model="same", small_model="same",
        )
        assert p.get_litellm_model("CHAT") == "openai/same"

    def test_unknown_intent_uses_big(self, provider):
        assert provider.get_litellm_model("UNKNOWN") == "openai/glm-4.7"

    def test_no_building_model_falls_back_to_big(self):
        p = ProviderConfig(
            name="test", provider_prefix="gemini", api_key="key",
            big_model="pro", small_model="flash",
        )
        assert p.get_litellm_model("BUILDING") == "gemini/pro"


# ── _load_fallback_providers ─────────────────────────────────────────


class TestLoadFallbackProviders:
    def test_no_env_vars_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            from server import _load_fallback_providers
            result = _load_fallback_providers()
            assert result == []

    def test_single_fallback(self):
        env = {
            "FALLBACK_1_PROVIDER": "openai",
            "FALLBACK_1_API_KEY": "test-key",
            "FALLBACK_1_BASE_URL": "https://api.groq.com/openai/v1",
            "FALLBACK_1_BIG_MODEL": "llama-70b",
            "FALLBACK_1_SMALL_MODEL": "llama-70b",
        }
        with patch.dict("os.environ", env, clear=True):
            from server import _load_fallback_providers
            result = _load_fallback_providers()
            assert len(result) == 1
            assert result[0].name == "fallback_1"
            assert result[0].provider_prefix == "openai"
            assert result[0].big_model == "llama-70b"

    def test_two_fallbacks(self):
        env = {
            "FALLBACK_1_PROVIDER": "openai",
            "FALLBACK_1_API_KEY": "key1",
            "FALLBACK_1_BIG_MODEL": "model1",
            "FALLBACK_2_PROVIDER": "openai",
            "FALLBACK_2_API_KEY": "key2",
            "FALLBACK_2_BIG_MODEL": "model2",
        }
        with patch.dict("os.environ", env, clear=True):
            from server import _load_fallback_providers
            result = _load_fallback_providers()
            assert len(result) == 2
            assert result[0].name == "fallback_1"
            assert result[1].name == "fallback_2"

    def test_gap_stops_loading(self):
        env = {
            "FALLBACK_1_PROVIDER": "openai",
            "FALLBACK_1_API_KEY": "key1",
            "FALLBACK_1_BIG_MODEL": "model1",
            # FALLBACK_2 missing
            "FALLBACK_3_PROVIDER": "openai",
            "FALLBACK_3_API_KEY": "key3",
            "FALLBACK_3_BIG_MODEL": "model3",
        }
        with patch.dict("os.environ", env, clear=True):
            from server import _load_fallback_providers
            result = _load_fallback_providers()
            assert len(result) == 1  # stops at gap

    def test_missing_api_key_stops(self):
        env = {
            "FALLBACK_1_PROVIDER": "openai",
            # no API_KEY
            "FALLBACK_1_BIG_MODEL": "model1",
        }
        with patch.dict("os.environ", env, clear=True):
            from server import _load_fallback_providers
            result = _load_fallback_providers()
            assert len(result) == 0
