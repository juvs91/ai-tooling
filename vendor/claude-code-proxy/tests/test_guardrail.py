# tests/test_guardrail.py
"""Tests for GuardrailTransformer."""
import pytest
from types import SimpleNamespace

from llm.pipeline import TransformContext
from llm.transformers.guardrail import (
    GuardrailTransformer,
    _build_tool_enforcement_prompt,
    _ANALYSIS_REASONING_PROMPT,
)


def _request(system=None, tools=None):
    return SimpleNamespace(system=system, tools=tools)


class TestBuildToolEnforcementPrompt:

    def test_no_tools(self):
        assert _build_tool_enforcement_prompt(None) == ""
        assert _build_tool_enforcement_prompt([]) == ""

    def test_with_tools(self):
        tools = [{"name": "Read"}, {"name": "Write"}, {"name": "Bash"}]
        prompt = _build_tool_enforcement_prompt(tools)
        assert "[tool-guard]" in prompt
        assert "3 tools" in prompt
        assert "Read" in prompt
        assert "Write" in prompt
        assert "Bash" in prompt

    def test_tools_without_names_skipped(self):
        tools = [{"name": "Read"}, {"other": "no-name"}, {"name": ""}]
        prompt = _build_tool_enforcement_prompt(tools)
        assert "1 tools" in prompt
        assert "Read" in prompt


class TestGuardrailTransformer:

    @pytest.mark.asyncio
    async def test_injects_guard_system(self):
        t = GuardrailTransformer("[proxy-guard] Do not fabricate.")
        req = _request(system=None)
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert "[proxy-guard] Do not fabricate." in req.system

    @pytest.mark.asyncio
    async def test_deduplicates_guard(self):
        t = GuardrailTransformer("GUARD")
        req = _request(system="GUARD\n\nExisting system")
        ctx = TransformContext()
        await t.transform(req, ctx)
        # Should not duplicate
        assert req.system.count("GUARD") == 1

    @pytest.mark.asyncio
    async def test_prepends_to_existing_string(self):
        t = GuardrailTransformer("GUARD")
        req = _request(system="You are helpful.")
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert req.system.startswith("GUARD")
        assert "You are helpful." in req.system

    @pytest.mark.asyncio
    async def test_prepends_to_list_system(self):
        t = GuardrailTransformer("GUARD")
        req = _request(system=[{"type": "text", "text": "Existing"}])
        ctx = TransformContext()
        await t.transform(req, ctx)
        assert isinstance(req.system, list)
        assert req.system[0]["text"] == "GUARD"

    @pytest.mark.asyncio
    async def test_no_analysis_enforcement_when_not_analysis(self):
        tools = [{"name": "Read"}, {"name": "Write"}]
        t = GuardrailTransformer("GUARD")
        req = _request(tools=tools)
        ctx = TransformContext(is_analysis=False)
        await t.transform(req, ctx)
        assert "[tool-guard]" not in (req.system or "")

    @pytest.mark.asyncio
    async def test_analysis_enforcement_injected(self):
        tools = [{"name": "Read"}, {"name": "Write"}]
        t = GuardrailTransformer("GUARD")
        req = _request(tools=tools)
        ctx = TransformContext(is_analysis=True, analysis_phase="READ")
        await t.transform(req, ctx)
        assert "[tool-guard]" in req.system

    @pytest.mark.asyncio
    async def test_analysis_enforcement_skipped_without_tools(self):
        t = GuardrailTransformer("GUARD")
        req = _request(tools=None)
        ctx = TransformContext(is_analysis=True, analysis_phase="READ")
        await t.transform(req, ctx)
        assert "[tool-guard]" not in (req.system or "")

    @pytest.mark.asyncio
    async def test_analysis_reasoning_prompt_injected(self):
        """Capa 1: reasoning enforcement injected for READ requests."""
        t = GuardrailTransformer("GUARD")
        req = _request(tools=None)
        ctx = TransformContext(is_analysis=True, analysis_phase="READ")
        await t.transform(req, ctx)
        assert "[code-analysis-guard]" in req.system
        assert "NEVER claim a file/function exists without reading" in req.system

    @pytest.mark.asyncio
    async def test_synthesizing_gets_synthesis_prompt(self):
        """SYNTHESIZING gets synthesis prompt, not analysis reasoning."""
        t = GuardrailTransformer("GUARD")
        req = _request(tools=None)
        ctx = TransformContext(is_analysis=True, analysis_phase="SYNTHESIZING")
        await t.transform(req, ctx)
        assert "[synthesis-guide]" in req.system
        assert "[code-analysis-guard]" not in req.system

    @pytest.mark.asyncio
    async def test_synthesizing_strips_tools(self):
        """SYNTHESIZING strips all tools to free context window."""
        tools = [{"name": "Read"}, {"name": "Grep"}, {"name": "Bash"}]
        t = GuardrailTransformer("GUARD")
        req = _request(tools=tools)
        ctx = TransformContext(is_analysis=True, analysis_phase="SYNTHESIZING")
        await t.transform(req, ctx)
        assert req.tools is not None  # tools kept — Override F handles phase reset if agent calls a tool
        assert "[tool-guard]" not in (req.system or "")
        assert "[synthesis-guide]" in req.system

    @pytest.mark.asyncio
    async def test_analysis_reasoning_prompt_with_tools(self):
        """Both tool-guard and code-analysis-guard injected when tools present."""
        tools = [{"name": "Read"}, {"name": "Grep"}]
        t = GuardrailTransformer("GUARD")
        req = _request(tools=tools)
        ctx = TransformContext(is_analysis=True, analysis_phase="READ")
        await t.transform(req, ctx)
        assert "[tool-guard]" in req.system
        assert "[code-analysis-guard]" in req.system

    @pytest.mark.asyncio
    async def test_no_reasoning_prompt_when_not_analysis(self):
        """Reasoning prompt NOT injected for non-analysis requests."""
        t = GuardrailTransformer("GUARD")
        req = _request(tools=None)
        ctx = TransformContext(is_analysis=False)
        await t.transform(req, ctx)
        assert "[code-analysis-guard]" not in (req.system or "")

    def test_reasoning_prompt_content(self):
        """Reasoning prompt contains key quality instructions."""
        assert "NEVER claim" in _ANALYSIS_REASONING_PROMPT
        assert "reading" in _ANALYSIS_REASONING_PROMPT
        assert "file:line" in _ANALYSIS_REASONING_PROMPT

    def test_name(self):
        assert GuardrailTransformer("x").name == "guardrail"
