# tests/test_fragile_model_plan_tools.py
"""Tests for FragileModelPlanToolsTransformer (ADR-0037 correction).

Root cause this transformer fixes, confirmed via a live fire test against the
real deployed proxy (docker logs):

    [deferred-tools] final-gate: stripped 2 plan-only tool(s) (intent=BUILD):
    EnterPlanMode, ExitPlanMode
    ...
    model_in=claude-sonnet-5 model_out=anthropic/kimi-k2

DeferredToolsTransformer's ADR-0037 fragile-model check runs BEFORE
ModelRouterTransformer resolves the client-sent alias ("claude-sonnet-5") to
the actual routed target ("anthropic/kimi-k2") — so the check always saw the
alias, never matched "kimi", and the eager-loading guarantee never fired for
the common alias-routed case. These tests prove the fix: a transformer that
runs AFTER ModelRouterTransformer, checking the FINAL resolved request.model.
"""
import pytest
from unittest.mock import MagicMock

from llm.transformers.fragile_model_plan_tools import FragileModelPlanToolsTransformer
from llm.transformers.deferred_tools import DeferredToolsTransformer, _PLAN_ONLY_TOOLS


def _make_request(model, tools=None):
    req = MagicMock()
    req.model = model
    req.tools = tools or []
    return req


def _make_ctx():
    return MagicMock()


def _tool_names(req):
    return {
        t["name"] if isinstance(t, dict) else getattr(t, "name", None)
        for t in req.tools
    }


class TestFragileModelPlanToolsTransformerUnit:
    @pytest.mark.asyncio
    async def test_fragile_model_gets_plan_tools_added(self):
        transformer = FragileModelPlanToolsTransformer()
        req = _make_request(model="anthropic/kimi-k2", tools=[])
        await transformer.transform(req, _make_ctx())

        names = _tool_names(req)
        assert "EnterPlanMode" in names
        assert "ExitPlanMode" in names

    @pytest.mark.asyncio
    async def test_non_fragile_model_untouched(self):
        transformer = FragileModelPlanToolsTransformer()
        req = _make_request(model="anthropic/claude-sonnet-5", tools=[])
        await transformer.transform(req, _make_ctx())

        assert req.tools == []

    @pytest.mark.asyncio
    async def test_idempotent_no_duplication_when_already_present(self):
        transformer = FragileModelPlanToolsTransformer()
        existing = [
            {"name": "EnterPlanMode", "description": "d", "input_schema": {}},
            {"name": "ExitPlanMode", "description": "d", "input_schema": {}},
        ]
        req = _make_request(model="kimi-k2", tools=list(existing))
        await transformer.transform(req, _make_ctx())

        names = [
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        ]
        assert names.count("EnterPlanMode") == 1
        assert names.count("ExitPlanMode") == 1

    @pytest.mark.asyncio
    async def test_partial_presence_only_adds_missing(self):
        transformer = FragileModelPlanToolsTransformer()
        req = _make_request(
            model="kimi-k2",
            tools=[{"name": "EnterPlanMode", "description": "d", "input_schema": {}}],
        )
        await transformer.transform(req, _make_ctx())

        names = _tool_names(req)
        assert "EnterPlanMode" in names
        assert "ExitPlanMode" in names
        assert len(req.tools) == 2

    @pytest.mark.asyncio
    async def test_none_model_does_not_crash(self):
        transformer = FragileModelPlanToolsTransformer()
        req = _make_request(model=None, tools=[])
        await transformer.transform(req, _make_ctx())  # must not raise
        assert req.tools == []


class TestOrderingBugRegression:
    """Reproduces the exact live-traffic scenario found via the fire test:
    client sends an alias ("claude-sonnet-5"), DeferredToolsTransformer's
    fragile check runs against that alias and fails to exempt the strip,
    ModelRouterTransformer then resolves the alias to the fragile target,
    and FragileModelPlanToolsTransformer must recover the tools that were
    incorrectly stripped."""

    @pytest.mark.asyncio
    async def test_alias_then_routing_then_recovery(self):
        # Step 1: DeferredToolsTransformer runs first, seeing the CLIENT alias.
        # No plan signal, BUILD intent — matches the real log line exactly.
        req = _make_request(model="claude-sonnet-5", tools=[])
        ctx = MagicMock()
        ctx.phase = "EXECUTE"
        ctx.intent = "BUILD"
        ctx.plan_mode_active = False
        ctx.session_id = ""

        deferred_transformer = DeferredToolsTransformer()
        await deferred_transformer.transform(req, ctx)

        # Confirms the bug's premise: the alias doesn't match "kimi", so the
        # fragile exemption did NOT fire — plan tools are absent.
        assert "EnterPlanMode" not in _tool_names(req)
        assert "ExitPlanMode" not in _tool_names(req)

        # Step 2: simulate ModelRouterTransformer resolving the alias to the
        # actual routed target — this is the only thing that changes.
        req.model = "anthropic/kimi-k2"

        # Step 3: the fix — FragileModelPlanToolsTransformer runs after
        # routing and recovers the tools using the now-correct model.
        fragile_transformer = FragileModelPlanToolsTransformer()
        await fragile_transformer.transform(req, ctx)

        names = _tool_names(req)
        assert "EnterPlanMode" in names, (
            "Post-routing guarantee must recover plan tools stripped earlier "
            "when DeferredToolsTransformer only saw the pre-routing alias"
        )
        assert "ExitPlanMode" in names

    @pytest.mark.asyncio
    async def test_non_fragile_routing_target_stays_stripped(self):
        """Regression: if the alias routes to a NON-fragile model, the strip
        must remain in effect — this transformer must not blanket-add plan
        tools for every request regardless of final target."""
        req = _make_request(model="claude-sonnet-5", tools=[])
        ctx = MagicMock()
        ctx.phase = "EXECUTE"
        ctx.intent = "BUILD"
        ctx.plan_mode_active = False
        ctx.session_id = ""

        await DeferredToolsTransformer().transform(req, ctx)
        assert "EnterPlanMode" not in _tool_names(req)

        req.model = "anthropic/claude-sonnet-5"  # routed to a non-fragile target

        await FragileModelPlanToolsTransformer().transform(req, ctx)
        assert "EnterPlanMode" not in _tool_names(req)
        assert "ExitPlanMode" not in _tool_names(req)


class TestRealPipelineOrdering:
    """Confirm the fix is actually wired into the real proxy pipeline, after
    ModelRouterTransformer — a structural check on the real
    build_request_pipeline(), the same pattern used elsewhere in this suite
    to catch registration/ordering bugs without needing network calls."""

    def test_registered_after_model_router(self):
        from config import load_config
        from proxy.proxy import build_request_pipeline

        cfg = load_config()
        pipeline = build_request_pipeline(cfg, models_differ=False)
        names = pipeline.transformer_names

        assert "fragile_model_plan_tools" in names
        assert "model_router" in names
        assert names.index("fragile_model_plan_tools") > names.index("model_router")
        assert names.index("fragile_model_plan_tools") > names.index("deferred_tools")
