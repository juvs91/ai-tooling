# tests/test_credential.py
"""Tests for CredentialTransformer + _inject_credentials."""
import pytest
from types import SimpleNamespace

from llm.pipeline import TransformContext
from llm.transformers.credential import CredentialTransformer, _inject_credentials
from config import ProviderCredentials, AnalysisConfig, RouteOverride


def _creds(
    openai_key="ok", openai_base=None,
    anthropic_key=None, anthropic_base=None,
    gemini_key=None, vertex=False, vertex_project="proj", vertex_location="us",
):
    return ProviderCredentials(
        openai_api_key=openai_key, openai_base_url=openai_base,
        anthropic_api_key=anthropic_key, anthropic_base_url=anthropic_base,
        gemini_api_key=gemini_key, use_vertex_auth=vertex,
        vertex_project=vertex_project, vertex_location=vertex_location,
    )


# ── _inject_credentials ────────────────────────────────────────────

class TestInjectCredentials:

    def test_openai_prefix(self):
        req = {}
        _inject_credentials(req, model="openai/glm-4.7", creds=_creds(openai_key="mykey", openai_base="http://z.ai"))
        assert req["api_key"] == "mykey"
        assert req["api_base"] == "http://z.ai"

    def test_openai_no_base_url(self):
        req = {}
        _inject_credentials(req, model="openai/gpt-4", creds=_creds(openai_key="k", openai_base=None))
        assert req["api_key"] == "k"
        assert "api_base" not in req

    def test_gemini_api_key(self):
        req = {}
        _inject_credentials(req, model="gemini/pro", creds=_creds(gemini_key="gk", vertex=False))
        assert req["api_key"] == "gk"
        assert "vertex_project" not in req

    def test_gemini_vertex_auth(self):
        req = {}
        _inject_credentials(req, model="gemini/pro", creds=_creds(vertex=True, vertex_project="p", vertex_location="l"))
        assert req["vertex_project"] == "p"
        assert req["vertex_location"] == "l"
        assert req["custom_llm_provider"] == "vertex_ai"

    def test_anthropic_prefix(self):
        req = {}
        _inject_credentials(req, model="anthropic/claude-3", creds=_creds(anthropic_key="ak", anthropic_base="http://ant"))
        assert req["api_key"] == "ak"
        # ADR-0005: endpoint path (default /v1/messages) is appended to anthropic_base.
        assert req["api_base"] == "http://ant/v1/messages"

    def test_anthropic_no_base(self):
        req = {}
        _inject_credentials(req, model="anthropic/claude-3", creds=_creds(anthropic_key="ak", anthropic_base=None))
        assert req["api_key"] == "ak"
        assert "api_base" not in req

    def test_bare_model_uses_anthropic(self):
        """Models without a known prefix fall through to anthropic creds."""
        req = {}
        _inject_credentials(req, model="claude-3-opus", creds=_creds(anthropic_key="ak"))
        assert req["api_key"] == "ak"


# ── CredentialTransformer ──────────────────────────────────────────

class TestCredentialTransformer:

    @pytest.mark.asyncio
    async def test_injects_openai_creds(self):
        t = CredentialTransformer(_creds(openai_key="mykey", openai_base="http://z.ai"))
        req = SimpleNamespace(model="openai/glm-4.7")
        ctx = TransformContext(litellm_request={})
        await t.transform(req, ctx)
        assert ctx.litellm_request["api_key"] == "mykey"
        assert ctx.litellm_request["api_base"] == "http://z.ai"

    @pytest.mark.asyncio
    async def test_injects_gemini_creds(self):
        t = CredentialTransformer(_creds(gemini_key="gk"))
        req = SimpleNamespace(model="gemini/pro")
        ctx = TransformContext(litellm_request={})
        await t.transform(req, ctx)
        assert ctx.litellm_request["api_key"] == "gk"

    @pytest.mark.asyncio
    async def test_injects_anthropic_creds(self):
        t = CredentialTransformer(_creds(anthropic_key="ak", anthropic_base="http://ant"))
        req = SimpleNamespace(model="anthropic/claude-3")
        ctx = TransformContext(litellm_request={})
        await t.transform(req, ctx)
        assert ctx.litellm_request["api_key"] == "ak"
        # ADR-0005: endpoint path (default /v1/messages) is appended to anthropic_base.
        assert ctx.litellm_request["api_base"] == "http://ant/v1/messages"

    @pytest.mark.asyncio
    async def test_empty_model_uses_anthropic_fallback(self):
        t = CredentialTransformer(_creds(anthropic_key="ak"))
        req = SimpleNamespace(model="")
        ctx = TransformContext(litellm_request={})
        await t.transform(req, ctx)
        assert ctx.litellm_request["api_key"] == "ak"

    def test_name(self):
        assert CredentialTransformer(_creds()).name == "credential"


# ── Route Override (cross-provider) ──────────────────────────────

class TestRouteOverride:
    """When ModelRouterTransformer sets ctx.route_override, CredentialTransformer
    should use it instead of model-prefix-based injection."""

    _DS_ROUTE = RouteOverride(
        provider="openai",
        api_key="sk-deepseek-key",
        base_url="https://api.deepseek.com/v1",
    )

    @pytest.mark.asyncio
    async def test_route_override_injects_key_and_base(self):
        t = CredentialTransformer(_creds(openai_key="primary-key", openai_base="http://z.ai"))
        req = SimpleNamespace(model="openai/deepseek-chat")
        ctx = TransformContext(litellm_request={}, route_override=self._DS_ROUTE)
        await t.transform(req, ctx)
        assert ctx.litellm_request["api_key"] == "sk-deepseek-key"
        assert ctx.litellm_request["api_base"] == "https://api.deepseek.com/v1"

    @pytest.mark.asyncio
    async def test_route_override_without_base_url(self):
        route = RouteOverride(provider="openai", api_key="sk-no-base")
        t = CredentialTransformer(_creds(openai_key="primary-key"))
        req = SimpleNamespace(model="openai/some-model")
        ctx = TransformContext(litellm_request={}, route_override=route)
        await t.transform(req, ctx)
        assert ctx.litellm_request["api_key"] == "sk-no-base"
        assert "api_base" not in ctx.litellm_request

    @pytest.mark.asyncio
    async def test_route_override_takes_precedence_over_prefix(self):
        """Even if model is openai/*, route_override credentials win."""
        t = CredentialTransformer(_creds(openai_key="openai-primary", openai_base="http://z.ai"))
        req = SimpleNamespace(model="openai/deepseek-chat")
        ctx = TransformContext(litellm_request={}, route_override=self._DS_ROUTE)
        await t.transform(req, ctx)
        # Should use route override, NOT primary openai creds
        assert ctx.litellm_request["api_key"] == "sk-deepseek-key"
        assert ctx.litellm_request["api_base"] == "https://api.deepseek.com/v1"

    @pytest.mark.asyncio
    async def test_no_route_override_uses_prefix_creds(self):
        """Without route_override, falls back to model-prefix-based injection."""
        t = CredentialTransformer(_creds(openai_key="openai-primary", openai_base="http://z.ai"))
        req = SimpleNamespace(model="openai/glm-4.7")
        ctx = TransformContext(litellm_request={})
        await t.transform(req, ctx)
        assert ctx.litellm_request["api_key"] == "openai-primary"
        assert ctx.litellm_request["api_base"] == "http://z.ai"

    @pytest.mark.asyncio
    async def test_analysis_override_beats_route_override(self):
        """Analysis override has highest priority, even over route_override."""
        analysis = AnalysisConfig(
            model="openai/analysis-model",
            api_key="sk-analysis-key",
            base_url="https://analysis.example.com",
            max_tokens=16384,
            max_refinements=0,
            quality_threshold=0.75,
        )
        t = CredentialTransformer(_creds(), analysis_cfg=analysis)
        req = SimpleNamespace(model="openai/analysis-model")
        ctx = TransformContext(
            litellm_request={},
            is_analysis=True,
            analysis_phase="SYNTHESIZING",
            route_override=self._DS_ROUTE,
        )
        await t.transform(req, ctx)
        # Analysis should win over route_override
        assert ctx.litellm_request["api_key"] == "sk-analysis-key"
        assert ctx.litellm_request["api_base"] == "https://analysis.example.com"

    @pytest.mark.asyncio
    async def test_route_override_with_gemini_model(self):
        """Route override works regardless of model prefix."""
        route = RouteOverride(
            provider="gemini", api_key="sk-groq-key", base_url="https://groq.example.com"
        )
        t = CredentialTransformer(_creds(gemini_key="primary-gemini"))
        req = SimpleNamespace(model="gemini/llama-3")
        ctx = TransformContext(litellm_request={}, route_override=route)
        await t.transform(req, ctx)
        assert ctx.litellm_request["api_key"] == "sk-groq-key"
        assert ctx.litellm_request["api_base"] == "https://groq.example.com"
