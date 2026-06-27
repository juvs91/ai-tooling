# llm/transformers/tool_allowlist.py
from __future__ import annotations

import logging
from typing import Any

from llm.pipeline import Transformer, TransformContext
from utils.utils import (
    get_tool_name,
    parse_allowlist,
    ensure_system_note,
    filter_tools_allowlist,
    normalize_tool_choice,
)
from utils.tool_utils import _CC_WORKFLOW_TOOL_NAMES, trim_tool_schemas
from config import PolicyConfig

logger = logging.getLogger(__name__)


def _parse_exclude(raw: str) -> set[str]:
    """Parse TOOL_EXCLUDE into a set of lowercase prefix patterns.

    Supports prefix globs: 'mcp__playwright__*' matches any tool starting with 'mcp__playwright__'.
    Exact names also supported: 'SomeTool' matches only that tool.
    """
    return {x.strip().lower() for x in (raw or "").split(",") if x.strip()}


def _matches_exclude(name: str, patterns: set[str]) -> bool:
    name_l = (name or "").lower()
    for p in patterns:
        if p.endswith("*"):
            if name_l.startswith(p[:-1]):
                return True
        elif name_l == p:
            return True
    return False


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

        # Blacklist: drop tools matching TOOL_EXCLUDE patterns (prefix globs, e.g. mcp__playwright__*)
        exclude = _parse_exclude(self._policy.tool_exclude_raw)
        if exclude and request.tools:
            kept, dropped_ex = [], []
            for t in request.tools:
                n = get_tool_name(t)
                if _matches_exclude(n, exclude):
                    dropped_ex.append(n)
                else:
                    kept.append(t)
            if dropped_ex:
                request.tools = kept or None
                logger.info(
                    "[tool-allowlist] Excluded %d tool(s) via TOOL_EXCLUDE: %s%s",
                    len(dropped_ex),
                    ", ".join(dropped_ex[:5]),
                    f" (+{len(dropped_ex) - 5} more)" if len(dropped_ex) > 5 else "",
                )

        # Trim descriptions to reduce token overhead (applies to all models)
        max_desc = self._policy.tool_schema_max_desc
        if max_desc > 0 and request.tools:
            request.tools = trim_tool_schemas(list(request.tools), max_desc)
            logger.debug(
                "[tool-allowlist] Trimmed descriptions: %d tools, max_desc=%d",
                len(request.tools), max_desc,
            )
