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

        if not allow:
            ctx.dropped_tools = [
                get_tool_name(t)
                for t in (getattr(request, "tools", None) or [])
                if get_tool_name(t)
            ]
            request.tools = None
            request.tool_choice = None
        else:
            request.tools, ctx.dropped_tools = filter_tools_allowlist(
                getattr(request, "tools", None), allow
            )
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
