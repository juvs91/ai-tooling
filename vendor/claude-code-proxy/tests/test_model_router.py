# tests/test_model_router.py
"""Tests for ModelRouterTransformer, is_ollama_base, system_chars, and intent classifier."""
import pytest
from types import SimpleNamespace
from unittest.mock import patch

from llm.pipeline import TransformContext
from llm.transformers.model_router import (
    ModelRouterTransformer,
    is_ollama_base,
    system_chars,
)
from router.llm_router import _regex_fallback_intent, choose_local_model
from router.model_mapper import map_claude_alias_to_target
from config import ModelRouting, ProviderCredentials, AnalysisConfig, RouteOverride


def _routing(preferred="openai", small="glm-flash", big="glm-4.7", building=None,
             ctx_window=200000, max_out=8192, small_route=None, building_route=None):
    return ModelRouting(
        preferred_provider=preferred,
        small_model=small, big_model=big,
        building_model=building or big,
        model_context_window=ctx_window,
        max_output_tokens=max_out,
        reasoning_max_tokens=16000,
        small_route=small_route,
        building_route=building_route,
    )


def _creds(base_url=None):
    return ProviderCredentials(
        openai_api_key="k", openai_base_url=base_url,
        anthropic_api_key=None, anthropic_base_url=None,
        gemini_api_key=None, use_vertex_auth=False,
        vertex_project="", vertex_location="",
    )


def _request(model="claude-sonnet-4-20250514", messages=None, system=None, tools=None, max_tokens=1024):
    msg = SimpleNamespace(role="user", content="hello")
    return SimpleNamespace(
        model=model, messages=messages or [msg],
        system=system, tools=tools, max_tokens=max_tokens,
    )


# ── Helpers ─────────────────────────────────────────────────────────

class TestIsOllamaBase:
    def test_ollama(self):
        assert is_ollama_base("http://localhost:11434") is True

    def test_not_ollama(self):
        assert is_ollama_base("https://api.z.ai/v4") is False

    def test_none(self):
        assert is_ollama_base(None) is False


class TestSystemChars:
    def test_none(self):
        assert system_chars(None) == 0

    def test_string(self):
        assert system_chars("hello world") == 11

    def test_list_with_text_attr(self):
        blocks = [SimpleNamespace(text="hello"), SimpleNamespace(text="world")]
        assert system_chars(blocks) == 10

    def test_list_with_dicts(self):
        blocks = [{"text": "hello"}, {"text": "world"}]
        assert system_chars(blocks) == 10

    def test_empty_list(self):
        assert system_chars([]) == 0


# ── ModelRouterTransformer ──────────────────────────────────────────

class TestOriginalModelPreservation:

    @pytest.mark.asyncio
    async def test_saves_original_model(self):
        t = ModelRouterTransformer(_routing(), _creds())
        req = _request(model="claude-sonnet-4-20250514")
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert req.original_model == "claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing(self):
        t = ModelRouterTransformer(_routing(), _creds())
        req = _request(model="claude-sonnet-4-20250514")
        req.original_model = "already-set"
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert req.original_model == "already-set"


class TestAliasMapping:

    @pytest.mark.asyncio
    async def test_maps_claude_to_target(self):
        t = ModelRouterTransformer(_routing(big="glm-4.7"), _creds())
        req = _request(model="claude-sonnet-4-20250514")
        ctx = TransformContext()
        await t.transform(req, ctx)
        # After alias mapping, model should contain provider prefix
        assert "glm" in req.model or "openai/" in req.model


class TestCloudRouting:
    """Non-Ollama (cloud) phase-based routing."""

    @pytest.mark.asyncio
    async def test_explore_routes_to_small(self):
        t = ModelRouterTransformer(
            _routing(small="glm-flash", big="glm-4.7"),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request()
        ctx = TransformContext(intent="CHAT", phase="EXPLORE")
        await t.transform(req, ctx)
        assert "glm-flash" in req.model

    @pytest.mark.asyncio
    async def test_execute_routes_to_building(self):
        t = ModelRouterTransformer(
            _routing(small="glm-flash", big="glm-4.7", building="glm-build"),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request()
        ctx = TransformContext(intent="BUILD", phase="EXECUTE")
        await t.transform(req, ctx)
        assert "glm-build" in req.model

    @pytest.mark.asyncio
    async def test_plan_stays_on_big(self):
        t = ModelRouterTransformer(
            _routing(small="glm-flash", big="glm-4.7"),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request()
        ctx = TransformContext(intent="PLAN", phase="PLAN")
        await t.transform(req, ctx)
        assert "glm-4.7" in req.model

    @pytest.mark.asyncio
    async def test_same_models_no_routing(self):
        """When all models are identical, no routing change."""
        t = ModelRouterTransformer(
            _routing(small="glm-4.7", big="glm-4.7", building="glm-4.7"),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request()
        ctx = TransformContext(intent="CHAT", phase="EXPLORE")
        await t.transform(req, ctx)
        assert "glm-4.7" in req.model


class TestOllamaRouting:

    @pytest.mark.asyncio
    async def test_ollama_uses_choose_local_model(self):
        with patch("llm.transformers.model_router.choose_local_model", return_value="qwen3:8b") as mock:
            t = ModelRouterTransformer(
                _routing(small="qwen3:1b", big="qwen3:8b"),
                _creds(base_url="http://localhost:11434"),
            )
            req = _request()
            ctx = TransformContext(intent="BUILD", approx_tokens=5000)
            await t.transform(req, ctx)
            mock.assert_called_once()
            assert req.model == "openai/qwen3:8b"

    def test_name(self):
        assert ModelRouterTransformer(_routing(), _creds()).name == "model_router"


# ── Analysis Model Upgrade ────────────────────────────────────────

def _analysis_cfg(model="deepseek/deepseek-reasoner", api_key="test-key", base_url=None, max_tokens=16384, max_refinements=0, quality_threshold=0.75, context_window=0):
    return AnalysisConfig(
        model=model, api_key=api_key, base_url=base_url,
        max_tokens=max_tokens, max_refinements=max_refinements,
        quality_threshold=quality_threshold, context_window=context_window,
    )


class TestAnalysisModelUpgrade:

    @pytest.mark.asyncio
    async def test_upgrades_model_for_synthesis(self):
        """ANALYSIS_MODEL only used during SYNTHESIZING phase."""
        t = ModelRouterTransformer(
            _routing(big="glm-4.7"), _creds(),
            analysis_cfg=_analysis_cfg(model="deepseek/deepseek-reasoner"),
        )
        req = _request()
        ctx = TransformContext(is_analysis=True, analysis_phase="SYNTHESIZING")
        await t.transform(req, ctx)
        assert req.model == "deepseek/deepseek-reasoner"

    @pytest.mark.asyncio
    async def test_analyzing_uses_big_model(self):
        """During READ phase, BIG_MODEL is used (not ANALYSIS_MODEL)."""
        t = ModelRouterTransformer(
            _routing(big="glm-4.7"), _creds(),
            analysis_cfg=_analysis_cfg(model="deepseek/deepseek-reasoner"),
        )
        req = _request()
        ctx = TransformContext(is_analysis=True, analysis_phase="READ")
        await t.transform(req, ctx)
        assert "glm-4.7" in req.model
        assert "deepseek-reasoner" not in req.model

    @pytest.mark.asyncio
    async def test_upgrades_max_tokens_for_synthesis(self):
        t = ModelRouterTransformer(
            _routing(), _creds(),
            analysis_cfg=_analysis_cfg(max_tokens=32768),
        )
        req = _request(max_tokens=1024)
        ctx = TransformContext(is_analysis=True, analysis_phase="SYNTHESIZING")
        await t.transform(req, ctx)
        assert req.max_tokens == 32768

    @pytest.mark.asyncio
    async def test_no_upgrade_when_not_analysis(self):
        t = ModelRouterTransformer(
            _routing(big="glm-4.7"), _creds(),
            analysis_cfg=_analysis_cfg(model="deepseek/deepseek-reasoner"),
        )
        req = _request()
        ctx = TransformContext(is_analysis=False, intent="CHAT", phase="EXPLORE")
        await t.transform(req, ctx)
        assert "deepseek" not in req.model

    @pytest.mark.asyncio
    async def test_no_upgrade_when_no_analysis_model(self):
        t = ModelRouterTransformer(
            _routing(big="glm-4.7"), _creds(),
            analysis_cfg=_analysis_cfg(model=""),
        )
        req = _request()
        ctx = TransformContext(is_analysis=True, intent="PLAN")
        await t.transform(req, ctx)
        # Empty analysis model → falls through to normal routing
        assert "glm-4.7" in req.model
        assert "deepseek" not in req.model

    @pytest.mark.asyncio
    async def test_no_upgrade_when_no_analysis_cfg(self):
        t = ModelRouterTransformer(_routing(big="glm-4.7"), _creds())
        req = _request()
        ctx = TransformContext(is_analysis=True, intent="PLAN")
        await t.transform(req, ctx)
        # No analysis config → falls through to normal routing
        assert "glm-4.7" in req.model
        assert "deepseek" not in req.model


# ── Cross-Provider Mixed Config (RouteOverride) ──────────────────

_DS_SMALL_ROUTE = RouteOverride(
    provider="openai", api_key="sk-deepseek-key",
    base_url="https://api.deepseek.com/v1",
)
_DS_BUILD_ROUTE = RouteOverride(
    provider="openai", api_key="sk-deepseek-key",
    base_url="https://api.deepseek.com/v1",
)


class TestMixedCrossProviderRouting:
    """
    Mixed config: GLM as BIG (Z.AI), DeepSeek Chat as SMALL, DeepSeek Reasoner as BUILDING.
    Each route has its own RouteOverride with provider/api_key/base_url.
    """

    @pytest.mark.asyncio
    async def test_explore_routes_to_deepseek_chat(self):
        """EXPLORE phase → openai/deepseek-chat with route override."""
        t = ModelRouterTransformer(
            _routing(
                small="deepseek-chat", big="glm-4.7",
                building="deepseek-reasoner",
                small_route=_DS_SMALL_ROUTE,
                building_route=_DS_BUILD_ROUTE,
            ),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request(model="claude-sonnet-4-20250514")
        ctx = TransformContext(intent="CHAT", phase="EXPLORE")
        await t.transform(req, ctx)
        assert req.model == "openai/deepseek-chat"
        assert ctx.route_override is _DS_SMALL_ROUTE

    @pytest.mark.asyncio
    async def test_execute_routes_to_deepseek_reasoner(self):
        """EXECUTE phase → openai/deepseek-reasoner with route override."""
        t = ModelRouterTransformer(
            _routing(
                small="deepseek-chat", big="glm-4.7",
                building="deepseek-reasoner",
                small_route=_DS_SMALL_ROUTE,
                building_route=_DS_BUILD_ROUTE,
            ),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request(model="claude-sonnet-4-20250514")
        ctx = TransformContext(intent="BUILD", phase="EXECUTE")
        await t.transform(req, ctx)
        assert req.model == "openai/deepseek-reasoner"
        assert ctx.route_override is _DS_BUILD_ROUTE

    @pytest.mark.asyncio
    async def test_plan_stays_on_glm_no_override(self):
        """PLAN phase → stays on openai/glm-4.7, no route override."""
        t = ModelRouterTransformer(
            _routing(
                small="deepseek-chat", big="glm-4.7",
                building="deepseek-reasoner",
                small_route=_DS_SMALL_ROUTE,
                building_route=_DS_BUILD_ROUTE,
            ),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request(model="claude-sonnet-4-20250514")
        ctx = TransformContext(intent="PLAN", phase="PLAN")
        await t.transform(req, ctx)
        assert req.model == "openai/glm-4.7"
        assert ctx.route_override is None

    @pytest.mark.asyncio
    async def test_no_route_override_same_provider(self):
        """Same-provider models: no RouteOverride needed, no ctx.route_override set."""
        t = ModelRouterTransformer(
            _routing(small="glm-flash", big="glm-4.7", building="glm-4.7"),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request(model="claude-sonnet-4-20250514")
        ctx = TransformContext(intent="CHAT", phase="EXPLORE")
        await t.transform(req, ctx)
        assert req.model == "openai/glm-flash"
        assert ctx.route_override is None

    @pytest.mark.asyncio
    async def test_route_override_with_different_provider_prefix(self):
        """RouteOverride can use a non-openai provider prefix."""
        groq_route = RouteOverride(provider="groq", api_key="gsk-key")
        t = ModelRouterTransformer(
            _routing(
                small="llama-3-70b", big="glm-4.7",
                small_route=groq_route,
            ),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request(model="claude-sonnet-4-20250514")
        ctx = TransformContext(intent="CHAT", phase="EXPLORE")
        await t.transform(req, ctx)
        assert req.model == "groq/llama-3-70b"
        assert ctx.route_override is groq_route


# ── Model Mapper Tests ────────────────────────────────────────────

class TestModelMapper:

    def test_sonnet_maps_to_big(self):
        result = map_claude_alias_to_target(
            "claude-sonnet-4-20250514",
            preferred_provider="openai",
            big_model="glm-4.7",
            small_model="deepseek-chat",
        )
        assert result == "openai/glm-4.7"

    def test_haiku_maps_to_small(self):
        result = map_claude_alias_to_target(
            "claude-haiku-4-5-20251001",
            preferred_provider="openai",
            big_model="glm-4.7",
            small_model="deepseek-chat",
        )
        assert result == "openai/deepseek-chat"

    def test_opus_maps_to_big(self):
        result = map_claude_alias_to_target(
            "claude-opus-4-20250514",
            preferred_provider="openai",
            big_model="glm-4.7",
            small_model="deepseek-chat",
        )
        assert result == "openai/glm-4.7"

    def test_empty_model_maps_to_small(self):
        result = map_claude_alias_to_target(
            "",
            preferred_provider="openai",
            big_model="glm-4.7",
            small_model="deepseek-chat",
        )
        assert result == "openai/deepseek-chat"

    def test_existing_prefix_preserved(self):
        result = map_claude_alias_to_target(
            "openai/gpt-4",
            preferred_provider="openai",
            big_model="glm-4.7",
            small_model="glm-flash",
        )
        assert result == "openai/gpt-4"


# ── Regex Intent Classifier ──────────────────────────────────────

class TestRegexFallbackIntent:
    """Verify regex classifier returns correct intent for typical messages."""

    def test_building_messages(self):
        assert _regex_fallback_intent("Fix the authentication bug") == "BUILD"
        assert _regex_fallback_intent("Implement the new endpoint") == "BUILD"
        assert _regex_fallback_intent("Arregla el error de login") == "BUILD"
        # "Ejecuta los tests" → BUILD (from "ejecuta" in BUILDING_RE).
        # LLM classifier path would return VERIFY; regex prefers the BUILD signal.
        assert _regex_fallback_intent("Ejecuta los tests de pytest") == "BUILD"
        assert _regex_fallback_intent("Refactor the proxy module") == "BUILD"

    def test_planning_messages(self):
        assert _regex_fallback_intent("Design the architecture for this feature") == "PLAN"
        assert _regex_fallback_intent("Review the RFC for the new API") == "PLAN"
        assert _regex_fallback_intent("Compare these two approaches") == "PLAN"
        assert _regex_fallback_intent("Haz un plan del módulo") == "PLAN"
        assert _regex_fallback_intent("Evalua las opciones") == "PLAN"

    def test_chat_messages(self):
        assert _regex_fallback_intent("Hello, how are you?") == "CHAT"
        assert _regex_fallback_intent("What does this function do?") == "CHAT"
        assert _regex_fallback_intent("Thanks for the help") == "CHAT"

    def test_ambiguous_prefers_planning(self):
        """When both planning and building keywords match, prefer PLAN (stronger model)."""
        assert _regex_fallback_intent("Plan the implementation of the fix") == "PLAN"


# ── choose_local_model (mixed config) ────────────────────────────

class TestChooseLocalModelMixed:
    """Verify local model selection with mixed model names."""

    def test_building_intent_returns_building_model(self):
        result = choose_local_model(
            messages=[{"role": "user", "content": "implement this"}],
            max_out=1024,
            approx_tokens=100,
            system_chars=100,
            tools_count=0,
            small_model="deepseek-chat",
            big_model="glm-4.7",
            building_model="deepseek-reasoner",
            intent="BUILD",
        )
        assert result == "deepseek-reasoner"

    def test_planning_intent_returns_big_model(self):
        result = choose_local_model(
            messages=[{"role": "user", "content": "design this"}],
            max_out=1024,
            approx_tokens=100,
            system_chars=100,
            tools_count=0,
            small_model="deepseek-chat",
            big_model="glm-4.7",
            building_model="deepseek-reasoner",
            intent="PLAN",
        )
        assert result == "glm-4.7"

    def test_chat_intent_returns_small_model(self):
        result = choose_local_model(
            messages=[{"role": "user", "content": "hi"}],
            max_out=100,
            approx_tokens=100,
            system_chars=100,
            tools_count=0,
            small_model="deepseek-chat",
            big_model="glm-4.7",
            building_model="deepseek-reasoner",
            intent="CHAT",
        )
        assert result == "deepseek-chat"


# ── Fix 4: effective_context_window resolution ──────────────────────

class TestEffectiveContextWindow:
    """ModelRouterTransformer should resolve effective_context_window."""

    @pytest.mark.asyncio
    async def test_route_override_with_context_window(self):
        """When RouteOverride has context_window, it should be used."""
        route = RouteOverride(provider="openai", api_key="k",
                              base_url="https://api.deepseek.com/v1",
                              context_window=64000)
        t = ModelRouterTransformer(
            _routing(small="deepseek-chat", big="glm-4.7",
                     small_route=route),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request()
        ctx = TransformContext(intent="CHAT", phase="EXPLORE")
        await t.transform(req, ctx)
        assert ctx.effective_context_window == 64000

    @pytest.mark.asyncio
    async def test_route_override_without_context_window_uses_global(self):
        """When RouteOverride has context_window=0, use global."""
        route = RouteOverride(provider="openai", api_key="k",
                              context_window=0)
        t = ModelRouterTransformer(
            _routing(small="deepseek-chat", big="glm-4.7",
                     ctx_window=200000, small_route=route),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request()
        ctx = TransformContext(intent="CHAT", phase="EXPLORE")
        await t.transform(req, ctx)
        assert ctx.effective_context_window == 200000

    @pytest.mark.asyncio
    async def test_no_route_override_uses_global(self):
        """No route override → effective = global model_context_window."""
        t = ModelRouterTransformer(
            _routing(ctx_window=200000),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request()
        ctx = TransformContext(intent="PLAN", phase="PLAN")
        await t.transform(req, ctx)
        assert ctx.effective_context_window == 200000

    @pytest.mark.asyncio
    async def test_building_route_context_window(self):
        """EXECUTE phase with building_route context_window."""
        route = RouteOverride(provider="openai", api_key="k",
                              context_window=128000)
        t = ModelRouterTransformer(
            _routing(small="glm-flash", big="glm-4.7",
                     building="MiniMax-M2.5",
                     ctx_window=200000, building_route=route),
            _creds(base_url="https://api.z.ai/v4"),
        )
        req = _request()
        ctx = TransformContext(intent="BUILD", phase="EXECUTE")
        await t.transform(req, ctx)
        assert ctx.effective_context_window == 128000


# ── Bug fix: haiku + PLAN phase → must use big_model, not small_model ──────────────
# Regression: PREFERRED_PROVIDER=anthropic + claude-haiku + PLAN produced
# "anthropic/deepseek-chat" (wrong provider+model). Z.AI Anthropic endpoint
# rejected it with code 1211 "Unknown Model". Fix: PLAN else branch uses
# _provider_prefix(preferred_provider) + big_model instead of derived prefix.

_MIXED_ROUTER_SMALL_ROUTE = RouteOverride(
    provider="openai", api_key="sk-deepseek-key",
    base_url="https://api.deepseek.com/v1", context_window=64000,
)
_MIXED_ROUTER_BUILD_ROUTE = RouteOverride(
    provider="openai", api_key="sk-minimax-key",
    base_url="https://api.minimax.io/v1", context_window=1000000,
)


class TestPlanPhaseHaikuBugFix:
    """
    Reproduces the production bug: cloud.mixed-router.env uses PREFERRED_PROVIDER=anthropic,
    BIG_MODEL=glm-4.7, SMALL_MODEL=deepseek-chat. CC sends claude-haiku-* for lightweight
    sub-tasks. Before fix, haiku + PLAN → "anthropic/deepseek-chat" → Z.AI "Unknown Model".
    """

    def _mixed_router_transformer(self):
        return ModelRouterTransformer(
            _routing(
                preferred="anthropic",
                small="deepseek-chat", big="glm-4.7",
                building="MiniMax-M2.5",
                ctx_window=200000,
                small_route=_MIXED_ROUTER_SMALL_ROUTE,
                building_route=_MIXED_ROUTER_BUILD_ROUTE,
            ),
            _creds(base_url="https://api.z.ai/v4"),
        )

    @pytest.mark.asyncio
    async def test_haiku_plan_uses_big_model_not_small(self):
        """haiku + PLAN must NOT produce anthropic/deepseek-chat."""
        t = self._mixed_router_transformer()
        req = _request(model="claude-haiku-4-5-20251001")
        ctx = TransformContext(intent="PLAN", phase="PLAN")
        await t.transform(req, ctx)
        assert req.model == "anthropic/glm-4.7"
        assert "deepseek-chat" not in req.model

    @pytest.mark.asyncio
    async def test_haiku_plan_prefix_from_env_not_from_mapped_model(self):
        """Prefix must come from preferred_provider (env), not from the intermediate mapped model."""
        t = self._mixed_router_transformer()
        req = _request(model="claude-haiku-4-5-20251001")
        ctx = TransformContext(intent="PLAN", phase="PLAN")
        await t.transform(req, ctx)
        # preferred_provider=anthropic → prefix must be "anthropic/"
        assert req.model.startswith("anthropic/")
        assert req.model == "anthropic/glm-4.7"

    @pytest.mark.asyncio
    async def test_sonnet_plan_unchanged(self):
        """sonnet + PLAN should still produce anthropic/glm-4.7 (idempotent)."""
        t = self._mixed_router_transformer()
        req = _request(model="claude-sonnet-4-20250514")
        ctx = TransformContext(intent="PLAN", phase="PLAN")
        await t.transform(req, ctx)
        assert req.model == "anthropic/glm-4.7"

    @pytest.mark.asyncio
    async def test_haiku_explore_still_routes_to_deepseek_openai(self):
        """EXPLORE phase with haiku must still use openai/deepseek-chat (unaffected by fix)."""
        t = self._mixed_router_transformer()
        req = _request(model="claude-haiku-4-5-20251001")
        ctx = TransformContext(intent="CHAT", phase="EXPLORE")
        await t.transform(req, ctx)
        assert req.model == "openai/deepseek-chat"
        assert ctx.route_override is _MIXED_ROUTER_SMALL_ROUTE

    @pytest.mark.asyncio
    async def test_haiku_execute_still_routes_to_minimax(self):
        """EXECUTE phase with haiku must still use openai/MiniMax-M2.5 (unaffected by fix)."""
        t = self._mixed_router_transformer()
        req = _request(model="claude-haiku-4-5-20251001")
        ctx = TransformContext(intent="BUILD", phase="EXECUTE")
        await t.transform(req, ctx)
        assert req.model == "openai/MiniMax-M2.5"
        assert ctx.route_override is _MIXED_ROUTER_BUILD_ROUTE

    @pytest.mark.asyncio
    async def test_plan_no_route_override_set(self):
        """PLAN phase must NOT set route_override (GLM lives on primary provider)."""
        t = self._mixed_router_transformer()
        req = _request(model="claude-haiku-4-5-20251001")
        ctx = TransformContext(intent="PLAN", phase="PLAN")
        await t.transform(req, ctx)
        assert ctx.route_override is None


# ── BUILDING_RE: tool_result/tool_use_id removed (protocol strings) ────────

class TestBuildingREToolIndicators:
    """tool_result/tool_use_id removed from BUILDING_RE — they are protocol
    strings, not user intent. Override A/B handle active-agent detection."""

    def test_tool_result_no_longer_matches_building(self):
        """Protocol string should NOT trigger BUILD intent."""
        assert _regex_fallback_intent("tool_result content here") == "CHAT"

    def test_tool_use_id_no_longer_matches_building(self):
        """Protocol string should NOT trigger BUILD intent."""
        assert _regex_fallback_intent("tool_use_id: toolu_abc123") == "CHAT"

    def test_still_matches_original_keywords(self):
        """Core BUILD keywords still work."""
        assert _regex_fallback_intent("implement the feature") == "BUILD"
        assert _regex_fallback_intent("fix the bug") == "BUILD"
        assert _regex_fallback_intent("refactor this code") == "BUILD"


# ── ANALYSIS_RE expansion: read+report pattern ──────────────────

class TestAnalysisREReadReportPattern:
    """Verify ANALYSIS_RE detects 'read/grep + tell me/explain + question word' patterns."""

    def test_t14_pattern(self):
        """T14-like: Read server.py, grep for X, tell me how it flows."""
        from router.llm_router import is_analysis_request
        text = (
            "Read the file server.py, then grep for 'quality_score' in the entire codebase. "
            "Use the Read and Grep tools. Tell me: 1) How many times quality_score appears, "
            "2) In which files it's used, 3) How it flows from request to response."
        )
        assert is_analysis_request(text) is True

    def test_read_explain_what(self):
        from router.llm_router import is_analysis_request
        assert is_analysis_request("Read all modules and explain what each one does") is True

    def test_grep_describe_how(self):
        from router.llm_router import is_analysis_request
        assert is_analysis_request("Grep for error handling and describe how errors propagate") is True

    def test_read_and_fix_does_not_match(self):
        """'Read and fix' should NOT match (no tell me/explain + question word)."""
        from router.llm_router import is_analysis_request
        # Pure building action — no "tell me/explain" reporting goal
        assert is_analysis_request("Read server.py and fix the bug on line 42") is False

    def test_simple_read_no_report_goal(self):
        """Simple 'read the file' without reporting goal should not match."""
        from router.llm_router import is_analysis_request
        assert is_analysis_request("Read the config file") is False

    def test_existing_patterns_still_work(self):
        """Existing ANALYSIS_RE patterns still work."""
        from router.llm_router import is_analysis_request
        assert is_analysis_request("Analyze the codebase architecture") is True
        assert is_analysis_request("Read all files in the project") is True
        assert is_analysis_request("exhaustive review of the system") is True


# ── P1: VERIFY priority fix ─────────────────────────────────────────

class TestVerifyPriorityFix:
    """VERIFY only fires when no BUILD/PLAN/READ signals are present.
    Previously VERIFY had highest priority — \\btest\\b swallowed everything."""

    def test_pure_verify_still_works(self):
        """Pure verification commands still return VERIFY."""
        assert _regex_fallback_intent("run the tests") == "VERIFY"
        assert _regex_fallback_intent("pytest tests/") == "VERIFY"
        assert _regex_fallback_intent("validate the changes") == "VERIFY"
        assert _regex_fallback_intent("corre los tests") == "VERIFY"

    def test_plan_with_test_returns_plan(self):
        """'Design the test architecture' → PLAN, not VERIFY."""
        assert _regex_fallback_intent("Design the test architecture") == "PLAN"

    def test_build_with_test_returns_build(self):
        """'Implement the test framework' → BUILD (from 'implement'), not VERIFY."""
        assert _regex_fallback_intent("Implement the test framework") == "BUILD"

    def test_fix_pytest_returns_build(self):
        """'Fix the pytest config' → BUILD (from 'fix'), not VERIFY."""
        assert _regex_fallback_intent("Fix the pytest config") == "BUILD"

    def test_analysis_with_test_returns_read(self):
        """'Analyze the test coverage exhaustively' → READ (analysis signal)."""
        assert _regex_fallback_intent("Analyze the test coverage exhaustively") == "READ"


# ── P4+P5: ANALYSIS_RE expanded targets + typo tolerance ────────────

class TestAnalysisREExpansion:
    """ANALYSIS_RE now covers more Spanish analysis targets and typos."""

    def test_analyze_classifier_spanish(self):
        """'analiza el clasificador' → analysis detected."""
        from router.llm_router import is_analysis_request
        assert is_analysis_request("analiza exhaustivamente el clasificador") is True

    def test_analyze_router(self):
        from router.llm_router import is_analysis_request
        assert is_analysis_request("analyze the router implementation") is True

    def test_analyze_pipeline(self):
        from router.llm_router import is_analysis_request
        assert is_analysis_request("analyze the pipeline component") is True

    def test_analyze_transformer_spanish(self):
        from router.llm_router import is_analysis_request
        assert is_analysis_request("analiza el transformer de intent") is True

    def test_analyze_server(self):
        from router.llm_router import is_analysis_request
        assert is_analysis_request("analyze the server module") is True

    def test_typo_exahustivo(self):
        """'exahustivo' (missing h) should still match."""
        from router.llm_router import is_analysis_request
        assert is_analysis_request("analisis exahustivo del código") is True

    def test_typo_exahustiva(self):
        from router.llm_router import is_analysis_request
        assert is_analysis_request("revisión exahustiva del proxy") is True

    def test_correct_spelling_still_works(self):
        from router.llm_router import is_analysis_request
        assert is_analysis_request("exhaustive review") is True
        assert is_analysis_request("análisis exhaustivo") is True
