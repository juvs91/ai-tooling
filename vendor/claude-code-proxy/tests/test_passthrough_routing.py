"""Tests for passthrough route override credential resolution."""
import pytest

from llm.pipeline import TransformContext
from proxy.proxy import _get_passthrough_credentials, _is_passthrough_compatible
from config import ProviderCredentials, ProxyConfig, ModelRouting, ClassifierConfig, CompressorConfig, PolicyConfig, AnalysisConfig, AdaptiveRoutingConfig, RouteOverride, ModelCosts


def _proxy_config(
    anthropic_base_url="https://api.kimi.com/coding/v1",
    anthropic_api_key="primary-key",
    passthrough_disabled=False,
    passthrough_require_prefix=True,
):
    return ProxyConfig(
        credentials=ProviderCredentials(
            openai_api_key="openai-key", openai_base_url=None,
            anthropic_api_key=anthropic_api_key, anthropic_base_url=anthropic_base_url,
            anthropic_endpoint_path="/messages",
            gemini_api_key=None, use_vertex_auth=False,
            vertex_project="", vertex_location="",
        ),
        routing=ModelRouting(
            preferred_provider="anthropic",
            small_model="kimi-for-coding", big_model="kimi-for-coding",
            building_model="kimi-for-coding",
            model_context_window=262144, max_output_tokens=131072,
            reasoning_max_tokens=16000,
        ),
        classifier=ClassifierConfig(
            model="", api_key="", base_url=None,
            timeout=3.0, max_consecutive_errors=3, circuit_reset_seconds=60.0,
        ),
        compressor=CompressorConfig(
            model="", api_key="", base_url=None,
            keep_recent=10, trigger_ratio=0.7,
            fallback_model=None, fallback_api_key=None, fallback_base_url=None,
            message_threshold=20, max_messages_ratio=0.85,
            max_tokens_ratio=0.85, tool_inflation_threshold=40,
            summary_trigger_ratio=0.60, recent_window_ratio=0.40,
        ),
        policy=PolicyConfig(
            tool_allowlist_raw="*", policy_note_in_system=True,
            max_input_tokens=0, hard_block_oversize=False,
            analysis_enforcement=False, tool_upgrade_threshold=5,
        ),
        analysis=AnalysisConfig(
            model="", api_key="", base_url=None,
            max_tokens=16384, max_refinements=1, quality_threshold=0.7,
        ),
        model_costs=ModelCosts(rates={}),
        max_retries=5, retry_base_delay=1.0,
        passthrough_disabled=passthrough_disabled,
        passthrough_require_prefix=passthrough_require_prefix,
        passthrough_timeout=120.0,
        passthrough_thinking_timeout=300.0,
        thinking_max_input_chars=0,
        adaptive=AdaptiveRoutingConfig(),
    )


class TestGetPassthroughCredentials:
    """Route overrides can supply their own anthropic api_key/base_url for passthrough."""

    def test_no_override_uses_primary(self):
        cfg = _proxy_config()
        ctx = TransformContext()
        base, key = _get_passthrough_credentials(cfg, ctx)
        assert base == cfg.credentials.anthropic_base_url
        assert key == cfg.credentials.anthropic_api_key

    def test_anthropic_override_wins(self):
        cfg = _proxy_config()
        route = RouteOverride(
            provider="anthropic", api_key="route-key",
            base_url="https://route.example.com",
        )
        ctx = TransformContext(route_override=route)
        base, key = _get_passthrough_credentials(cfg, ctx)
        assert base == "https://route.example.com"
        assert key == "route-key"

    def test_anthropic_override_falls_back_to_primary_base_url(self):
        cfg = _proxy_config()
        route = RouteOverride(provider="anthropic", api_key="route-key")
        ctx = TransformContext(route_override=route)
        base, key = _get_passthrough_credentials(cfg, ctx)
        assert base == cfg.credentials.anthropic_base_url
        assert key == "route-key"

    def test_non_anthropic_override_ignored(self):
        cfg = _proxy_config()
        route = RouteOverride(
            provider="openai", api_key="route-key",
            base_url="https://route.example.com",
        )
        ctx = TransformContext(route_override=route)
        base, key = _get_passthrough_credentials(cfg, ctx)
        assert base == cfg.credentials.anthropic_base_url
        assert key == cfg.credentials.anthropic_api_key


class TestIsPassthroughCompatible:
    """Passthrough compatibility considers route override credentials."""

    def test_anthropic_model_with_primary_creds(self):
        cfg = _proxy_config()
        ctx = TransformContext()
        assert _is_passthrough_compatible("anthropic/kimi-for-coding", cfg, ctx) is True

    def test_anthropic_model_with_route_override_only(self):
        cfg = _proxy_config(anthropic_api_key="")  # no primary key
        route = RouteOverride(
            provider="anthropic", api_key="route-key",
            base_url="https://route.example.com",
        )
        ctx = TransformContext(route_override=route)
        assert _is_passthrough_compatible("anthropic/kimi-for-coding", cfg, ctx) is True

    def test_openai_model_not_passthrough(self):
        cfg = _proxy_config()
        ctx = TransformContext()
        assert _is_passthrough_compatible("openai/gpt-4", cfg, ctx) is False

    def test_passthrough_disabled(self):
        cfg = _proxy_config(passthrough_disabled=True)
        ctx = TransformContext()
        assert _is_passthrough_compatible("anthropic/kimi-for-coding", cfg, ctx) is False

    def test_bare_model_requires_prefix(self):
        cfg = _proxy_config(passthrough_require_prefix=True)
        ctx = TransformContext()
        assert _is_passthrough_compatible("kimi-for-coding", cfg, ctx) is False

    def test_bare_model_without_require_prefix(self):
        cfg = _proxy_config(passthrough_require_prefix=False)
        ctx = TransformContext()
        assert _is_passthrough_compatible("kimi-for-coding", cfg, ctx) is True
