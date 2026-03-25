# llm/transformers/tool_allowlist.py
from __future__ import annotations

from typing import Any

from llm.pipeline import Transformer, TransformContext
from utils.utils import (
    get_tool_name,
    parse_allowlist,
    ensure_system_note,
    filter_tools_allowlist,
    normalize_tool_choice,
)
from utils.tool_utils import _CC_WORKFLOW_TOOL_NAMES
from config import PolicyConfig


class ToolAllowlistTransformer(Transformer):
    """Filter tools by allowlist, inject policy note for dropped tools."""

    @property
    def name(self) -> str:
        return "tool_allowlist"

    def __init__(self, policy_cfg: PolicyConfig) -> None:
        self._policy = policy_cfg

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        allow = parse_allowlist(self._policy.tool_allowlist_raw)

        all_tools = getattr(request, "tools", None) or []
        # Separate proxy-injected CC workflow tools from user-facing tools.
        # CC workflow tools must survive the allowlist filter — they are injected
        # by DeferredToolsTransformer for plan-mode interactions and must reach
        # the model regardless of the configured tool allowlist.
        cc_tools = [t for t in all_tools if get_tool_name(t) in _CC_WORKFLOW_TOOL_NAMES]
        user_tools = [t for t in all_tools if get_tool_name(t) not in _CC_WORKFLOW_TOOL_NAMES]

        if not allow:
            ctx.dropped_tools = [get_tool_name(t) for t in user_tools if get_tool_name(t)]
            request.tools = cc_tools or None
            request.tool_choice = None
        else:
            filtered_tools, ctx.dropped_tools = filter_tools_allowlist(user_tools, allow)
            request.tools = (filtered_tools or []) + cc_tools or None
            request.tool_choice = normalize_tool_choice(
                getattr(request, "tool_choice", None),
                getattr(request, "tools", None),
            )

        if ctx.dropped_tools and self._policy.policy_note_in_system:
            ensure_system_note(
                request,
                f"[proxy-policy] Tools not allowed and were removed: "
                f"{', '.join(ctx.dropped_tools)}. "
                f"Allowed: {', '.join(sorted(allow))}.",
            )
