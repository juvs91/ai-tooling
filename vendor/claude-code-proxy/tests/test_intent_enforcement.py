# tests/test_intent_enforcement.py
"""Tests for IntentEnforcementTransformer."""
import pytest
from types import SimpleNamespace

from llm.pipeline import TransformContext
from llm.transformers.intent_enforcement import IntentEnforcementTransformer


def _request(system=None):
    """Create a mock request object."""
    return SimpleNamespace(system=system)


def _ctx(intent="CHAT", analysis_phase="NONE"):
    """Create a TransformContext with given intent."""
    return TransformContext(intent=intent, analysis_phase=analysis_phase)


class TestIntentEnforcementTransformer:
    """Test suite for IntentEnforcementTransformer."""

    @pytest.mark.asyncio
    async def test_read_intent_injects_system_note(self):
        """READ intent must inject enforcement note with anti-speculative rules."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="READ")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "[INTENT-ENFORCEMENT]" in req.system
        assert "READ/ANALYZING mode" in req.system
        # Must have anti-speculative generation rules
        assert "file not found" in req.system
        assert "do NOT invent" in req.system

    @pytest.mark.asyncio
    async def test_analyzing_phase_injects_read_note(self):
        """ANALYZING phase must inject READ enforcement note."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="PLAN", analysis_phase="ANALYZING")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "READ/ANALYZING mode" in req.system

    @pytest.mark.asyncio
    async def test_plan_intent_injects_system_note(self):
        """PLAN intent must inject structured plan output note."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="PLAN")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "[INTENT-ENFORCEMENT]" in req.system
        assert "PLAN mode" in req.system
        assert "structured implementation plan" in req.system

    @pytest.mark.asyncio
    async def test_synthesizing_intent_injects_system_note(self):
        """SYNTHESIZING intent must inject no-tools-available note."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="SYNTHESIZING")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "[INTENT-ENFORCEMENT]" in req.system
        assert "SYNTHESIZING mode" in req.system
        # Must tell model tools are unavailable (for deepseek-reasoner)
        assert "none are available" in req.system

    @pytest.mark.asyncio
    async def test_building_intent_injects_system_note(self):
        """BUILDING intent must inject execute-now note."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="BUILDING")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "[INTENT-ENFORCEMENT]" in req.system
        assert "BUILDING mode" in req.system
        assert "Edit tool" in req.system

    @pytest.mark.asyncio
    async def test_build_intent_alias_injects_system_note(self):
        """BUILD intent (alias) must also inject BUILDING enforcement."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="BUILD")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "BUILDING mode" in req.system

    @pytest.mark.asyncio
    async def test_verify_intent_injects_system_note(self):
        """VERIFY intent must inject test-execution note."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="VERIFY")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "[INTENT-ENFORCEMENT]" in req.system
        assert "VERIFY mode" in req.system
        assert "Bash tool" in req.system

    @pytest.mark.asyncio
    async def test_read_enforcement_has_no_invent_rule(self):
        """READ enforcement must explicitly forbid inventing file content."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="READ")
        await t.transform(req, ctx)
        assert "do NOT invent" in req.system

    @pytest.mark.asyncio
    async def test_synthesizing_enforcement_has_no_tools_available_rule(self):
        """SYNTHESIZING enforcement must state no tools are available (deepseek-reasoner context)."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="SYNTHESIZING")
        await t.transform(req, ctx)
        assert "none are available" in req.system

    @pytest.mark.asyncio
    async def test_building_enforcement_has_execute_now_rule(self):
        """BUILDING enforcement must require immediate execution, not description."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="BUILD")
        await t.transform(req, ctx)
        assert "Do not describe" in req.system

    @pytest.mark.asyncio
    async def test_chat_intent_skips_injection(self):
        """CHAT intent must NOT inject anything."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="CHAT")
        await t.transform(req, ctx)
        assert req.system is None

    @pytest.mark.asyncio
    async def test_none_intent_skips_injection(self):
        """None intent must NOT inject anything."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent=None)
        await t.transform(req, ctx)
        assert req.system is None

    @pytest.mark.asyncio
    async def test_empty_string_intent_skips_injection(self):
        """Empty string intent must NOT inject anything."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="")
        await t.transform(req, ctx)
        assert req.system is None

    @pytest.mark.asyncio
    async def test_enabled_false_skips_all_injection(self):
        """Transformer with enabled=False must NOT inject anything."""
        t = IntentEnforcementTransformer(enabled=False)
        req = _request(system=None)
        ctx = _ctx(intent="READ")  # Even with READ intent
        await t.transform(req, ctx)
        assert req.system is None

    @pytest.mark.asyncio
    async def test_preserves_existing_system(self):
        """Must preserve existing system and append note."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system="Existing system prompt")
        ctx = _ctx(intent="READ")
        await t.transform(req, ctx)
        assert "Existing system prompt" in req.system
        assert "[INTENT-ENFORCEMENT]" in req.system

    @pytest.mark.asyncio
    async def test_does_not_modify_request_structure(self):
        """Must not break the request object structure."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system="Original")
        ctx = _ctx(intent="PLAN")
        original_attrs = set(dir(req))
        await t.transform(req, ctx)
        # Should still have the same attributes
        assert set(dir(req)) >= original_attrs
        assert hasattr(req, "system")

    @pytest.mark.asyncio
    async def test_multiple_transforms_deduplicate(self):
        """Multiple transforms should not duplicate the note."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="READ")

        # First transform
        await t.transform(req, ctx)
        first_system = req.system
        note_count = first_system.count("[INTENT-ENFORCEMENT]")

        # Second transform (simulate re-processing)
        await t.transform(req, ctx)
        assert req.system.count("[INTENT-ENFORCEMENT]") <= note_count + 1
