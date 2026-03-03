# tests/test_tool_allowlist.py
"""Tests for ToolAllowlistTransformer."""
import pytest
from types import SimpleNamespace

from llm.pipeline import TransformContext
from llm.transformers.tool_allowlist import ToolAllowlistTransformer
from config import PolicyConfig


def _policy(allowlist="*", note=True):
    return PolicyConfig(
        tool_allowlist_raw=allowlist, policy_note_in_system=note,
        max_input_tokens=0, hard_block_oversize=False,
        analysis_enforcement=False, tool_upgrade_threshold=5,
        guard_system="",
    )


def _request(tools=None, tool_choice=None, system=None):
    return SimpleNamespace(tools=tools, tool_choice=tool_choice, system=system)


class TestWildcardAllowlist:
    """TOOL_ALLOWLIST=* → all tools kept."""

    @pytest.mark.asyncio
    async def test_all_tools_kept(self):
        tools = [{"name": "Read"}, {"name": "Write"}, {"name": "Bash"}]
        t = ToolAllowlistTransformer(_policy("*"))
        req = _request(tools=tools)
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert len(req.tools) == 3
        assert ctx.dropped_tools == []

    @pytest.mark.asyncio
    async def test_no_policy_note_injected(self):
        tools = [{"name": "Read"}]
        t = ToolAllowlistTransformer(_policy("*"))
        req = _request(tools=tools)
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert req.system is None


class TestEmptyAllowlist:
    """TOOL_ALLOWLIST= (empty) → all tools dropped."""

    @pytest.mark.asyncio
    async def test_all_tools_dropped(self):
        tools = [{"name": "Read"}, {"name": "Write"}]
        t = ToolAllowlistTransformer(_policy(""))
        req = _request(tools=tools)
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert req.tools is None
        assert req.tool_choice is None
        assert set(ctx.dropped_tools) == {"Read", "Write"}


class TestSelectiveAllowlist:
    """TOOL_ALLOWLIST=Read,Write → only those kept."""

    @pytest.mark.asyncio
    async def test_filters_correctly(self):
        tools = [{"name": "Read"}, {"name": "Write"}, {"name": "Bash"}, {"name": "Glob"}]
        t = ToolAllowlistTransformer(_policy("Read,Write"))
        req = _request(tools=tools)
        ctx = TransformContext()
        await t.transform(req, ctx)
        kept_names = [t["name"] for t in req.tools]
        assert kept_names == ["Read", "Write"]
        assert set(ctx.dropped_tools) == {"Bash", "Glob"}

    @pytest.mark.asyncio
    async def test_policy_note_injected(self):
        tools = [{"name": "Read"}, {"name": "Bash"}]
        t = ToolAllowlistTransformer(_policy("Read"))
        req = _request(tools=tools)
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert "[proxy-policy]" in (req.system or "")
        assert "Bash" in (req.system or "")

    @pytest.mark.asyncio
    async def test_no_policy_note_when_disabled(self):
        tools = [{"name": "Read"}, {"name": "Bash"}]
        t = ToolAllowlistTransformer(_policy("Read", note=False))
        req = _request(tools=tools)
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert req.system is None

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        tools = [{"name": "Read"}, {"name": "WRITE"}]
        t = ToolAllowlistTransformer(_policy("read,write"))
        req = _request(tools=tools)
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert len(req.tools) == 2
        assert ctx.dropped_tools == []


class TestToolChoiceNormalization:

    @pytest.mark.asyncio
    async def test_tool_choice_cleared_when_no_tools(self):
        t = ToolAllowlistTransformer(_policy(""))
        req = _request(tools=[{"name": "Read"}], tool_choice={"type": "tool", "name": "Read"})
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert req.tool_choice is None

    @pytest.mark.asyncio
    async def test_tool_choice_for_dropped_tool_becomes_auto(self):
        t = ToolAllowlistTransformer(_policy("Write"))
        req = _request(
            tools=[{"name": "Read"}, {"name": "Write"}],
            tool_choice={"type": "tool", "name": "Read"},
        )
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert req.tool_choice == {"type": "auto"}


class TestNoTools:
    """Request without tools → no-op."""

    @pytest.mark.asyncio
    async def test_no_tools_noop(self):
        t = ToolAllowlistTransformer(_policy("Read"))
        req = _request(tools=None)
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert req.tools is None
        assert ctx.dropped_tools == []

    def test_name(self):
        assert ToolAllowlistTransformer(_policy()).name == "tool_allowlist"
