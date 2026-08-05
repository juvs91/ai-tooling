# llm/transformers/fragile_model_plan_tools.py
"""Fragile-Model Plan Tools Guarantee — Post-Routing (ADR-0037 correction).

`DeferredToolsTransformer`'s Step 4b / final-gate exemption (ADR-0037) checks
`is_fragile_orchestration_model(request.model)` — but `DeferredToolsTransformer`
runs BEFORE `ModelRouterTransformer` in `build_request_pipeline`
(proxy/proxy.py), and `ModelRouterTransformer` is the ONLY place `request.model`
gets rewritten from the client-sent alias (e.g. "claude-sonnet-5") to the
actual routed target (e.g. "anthropic/kimi-k2").

Confirmed via a live fire test against the real deployed proxy (docker logs):

    [deferred-tools] final-gate: stripped 2 plan-only tool(s) (intent=BUILD):
    EnterPlanMode, ExitPlanMode
    ...
    model_in=claude-sonnet-5 model_out=anthropic/kimi-k2

The fragile check saw "claude-sonnet-5" (no "kimi" substring) and never
exempted the strip — ADR-0037's entire mechanism was a no-op in production
for the common alias-routed case, despite 1290 passing unit/integration
tests, because every test constructed its mock request with `model="kimi-k2"`
set directly, never exercising the real alias→routing ordering.

This transformer is the actual fix: it runs AFTER `ModelRouterTransformer`,
when `request.model` is finally correct, and re-applies the same guarantee.
`DeferredToolsTransformer`'s own Step 4b/gate exemption is left in place —
it still covers the rarer case where a client requests the fragile model by
its real name directly (no alias remap needed) — but this transformer is the
one that matters for the common case and is the authoritative, always-correct
enforcement point.
"""
from __future__ import annotations

import logging
from typing import Any

from llm.pipeline import Transformer, TransformContext
from llm.transformers.deferred_tools import (
    _CC_TOOL_DESCRIPTIONS,
    _CC_TOOL_SCHEMAS,
    _PLAN_ONLY_TOOLS,
    _tool_name,
)
from utils.tool_utils import is_fragile_orchestration_model

logger = logging.getLogger(__name__)


class FragileModelPlanToolsTransformer(Transformer):
    """Ensures EnterPlanMode/ExitPlanMode are present in request.tools for
    fragile models, checked against the FINAL, correctly-routed request.model.
    """

    @property
    def name(self) -> str:
        return "fragile_model_plan_tools"

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        model = getattr(request, "model", None)
        if not is_fragile_orchestration_model(model):
            return

        existing_names = {n for t in (request.tools or []) if (n := _tool_name(t))}
        missing = [name for name in _PLAN_ONLY_TOOLS if name not in existing_names]
        if not missing:
            return

        new_defs = [
            {
                "name": name,
                "description": _CC_TOOL_DESCRIPTIONS.get(
                    name,
                    f"Claude Code built-in workflow tool: {name}. Use the input schema.",
                ),
                "input_schema": _CC_TOOL_SCHEMAS.get(name, {"type": "object", "properties": {}}),
            }
            for name in missing
        ]
        request.tools = list(request.tools or []) + new_defs
        logger.info(
            "[fragile-model-plan-tools] post-routing guarantee (model=%s): added %s",
            model, missing,
        )
