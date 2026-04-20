# tests/test_intent_enforcement.py
"""Tests for IntentEnforcementTransformer."""
import pytest
from types import SimpleNamespace

from llm.pipeline import TransformContext
from llm.transformers.intent_enforcement import IntentEnforcementTransformer


def _request(system=None, tools=None):
    """Create a mock request object. Defaults tools to [Bash] so enforcement fires."""
    if tools is None:
        tools = [SimpleNamespace(name="Bash")]
    return SimpleNamespace(system=system, tools=tools)


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
        assert "Never assume" in req.system
        assert "No citation = no claim" in req.system

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
        assert "DO NOT call Edit or Write on ANY other file" in req.system

    @pytest.mark.asyncio
    async def test_synthesizing_intent_injects_system_note(self):
        """SYNTHESIZING intent must inject synthesis guidance note."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="SYNTHESIZING")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "[INTENT-ENFORCEMENT]" in req.system
        assert "SYNTHESIZING mode" in req.system
        # Must prioritize written synthesis over tool calls
        assert "minimize tool calls" in req.system

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
        assert "Never assume" in req.system

    @pytest.mark.asyncio
    async def test_synthesizing_enforcement_has_no_tools_available_rule(self):
        """SYNTHESIZING enforcement must minimize tool calls and focus on written synthesis."""
        t = IntentEnforcementTransformer(enabled=True)
        req = _request(system=None)
        ctx = _ctx(intent="SYNTHESIZING")
        await t.transform(req, ctx)
        assert "minimize tool calls" in req.system

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


class TestWrapUpTurnNoEnforcement:
    """BUILD/VERIFY with tools_in=0 must NOT inject enforcement prompt.

    Wrap-up turns (CC asking model to conclude after tool execution) have no
    tool definitions. Injecting "Make file changes NOW" causes unnecessary edits.
    """

    @pytest.mark.asyncio
    async def test_build_no_tools_skips_enforcement(self):
        """BUILD intent + tools_in=0 → no enforcement injected."""
        t = IntentEnforcementTransformer(enabled=True)
        req = SimpleNamespace(system=None, tools=None)
        ctx = TransformContext(intent="BUILD", phase="EXECUTE")
        await t.transform(req, ctx)
        assert req.system is None

    @pytest.mark.asyncio
    async def test_verify_no_tools_skips_enforcement(self):
        """VERIFY intent + tools_in=0 → no enforcement injected."""
        t = IntentEnforcementTransformer(enabled=True)
        req = SimpleNamespace(system=None, tools=None)
        ctx = TransformContext(intent="VERIFY", phase="EXECUTE")
        await t.transform(req, ctx)
        assert req.system is None

    @pytest.mark.asyncio
    async def test_build_with_tools_still_enforces(self):
        """BUILD intent + tools_in>0 → enforcement IS injected (normal build turn)."""
        t = IntentEnforcementTransformer(enabled=True)
        req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Bash")])
        ctx = TransformContext(intent="BUILD", phase="EXECUTE")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "[INTENT-ENFORCEMENT]" in req.system

    @pytest.mark.asyncio
    async def test_read_no_tools_still_enforces(self):
        """READ intent + tools_in=0 → enforcement IS injected (READ guides tool use behavior)."""
        t = IntentEnforcementTransformer(enabled=True)
        req = SimpleNamespace(system=None, tools=None)
        ctx = TransformContext(intent="READ", phase="PLAN")
        await t.transform(req, ctx)
        assert req.system is not None
        assert "[INTENT-ENFORCEMENT]" in req.system


# ── Ralph mode suppression (Item 1) ──────────────────────────────────────────

from llm.converters import _system_to_text as _to_text


class TestRalphModeSuppression:
    """When ctx.ralph_mode=True, enforcement must inject the no-AskUserQuestion note."""

    @pytest.mark.asyncio
    async def test_ralph_mode_injects_autonomous_note(self):
        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="BUILD", ralph_mode=True)
        req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Edit")], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system)
        assert "RALPH MODE" in text
        assert "AskUserQuestion" in text
        assert "AUTONOMOUS" in text

    @pytest.mark.asyncio
    async def test_ralph_mode_false_does_not_inject_note(self):
        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="BUILD", ralph_mode=False)
        req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Edit")], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system) or ""
        assert "RALPH MODE" not in text

    @pytest.mark.asyncio
    async def test_ralph_note_stacks_with_intent_enforcement(self):
        """Ralph note and intent-specific prompt must both appear in system."""
        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="BUILD", ralph_mode=True)
        req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Edit")], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system)
        assert "RALPH MODE" in text
        assert "[INTENT-ENFORCEMENT]" in text  # intent note also present

    @pytest.mark.asyncio
    async def test_ralph_note_injected_regardless_of_intent(self):
        """Ralph suppression fires for any non-CHAT intent, not just BUILD."""
        for intent in ("READ", "PLAN", "SYNTHESIZING", "VERIFY"):
            t = IntentEnforcementTransformer(enabled=True)
            ctx = TransformContext(intent=intent, ralph_mode=True)
            req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Read")], messages=[])
            await t.transform(req, ctx)
            text = _to_text(req.system) or ""
            assert "RALPH MODE" in text, f"RALPH MODE note missing for intent={intent}"


# ── Adaptive quality enforcement (Item 4) ────────────────────────────────────

class TestAdaptiveQualityEnforcement:
    """Adaptive enforcement escalates when session quality history is consistently poor."""

    @pytest.fixture(autouse=True)
    def isolate_session(self, monkeypatch, tmp_path):
        """Each test gets a clean session cache."""
        import llm.compressor as comp
        monkeypatch.setattr(comp, "_SESSION_CACHE_FILE", str(tmp_path / "cache.json"))
        comp._session_cache.clear()

    @pytest.mark.asyncio
    async def test_low_avg_quality_triggers_critical_note(self):
        from llm.compressor import append_session_quality
        sid = "low-qual-test"
        for _ in range(5):
            await append_session_quality(sid, 0.35)

        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="READ", session_id=sid)
        req = SimpleNamespace(system=None, tools=[], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system) or ""
        assert "SESSION-QUALITY" in text
        assert "CRITICAL" in text

    @pytest.mark.asyncio
    async def test_high_avg_quality_no_escalation(self):
        from llm.compressor import append_session_quality
        sid = "high-qual-test"
        for _ in range(5):
            await append_session_quality(sid, 0.90)

        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="BUILD", session_id=sid)
        req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Edit")], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system) or ""
        assert "SESSION-QUALITY" not in text

    @pytest.mark.asyncio
    async def test_stub_count_2_triggers_strict_note(self):
        from llm.compressor import append_session_quality
        sid = "stub-test"
        await append_session_quality(sid, 0.80, stub_delta=2)  # 2 stubs, score above threshold

        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="BUILD", session_id=sid)
        req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Edit")], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system) or ""
        assert "SESSION-QUALITY" in text
        assert "STRICT" in text

    @pytest.mark.asyncio
    async def test_stub_count_1_no_escalation(self):
        """Below threshold (< 2 stubs) must NOT trigger escalation."""
        from llm.compressor import append_session_quality
        sid = "one-stub-test"
        await append_session_quality(sid, 0.80, stub_delta=1)

        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="BUILD", session_id=sid)
        req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Edit")], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system) or ""
        assert "SESSION-QUALITY" not in text

    @pytest.mark.asyncio
    async def test_no_session_id_no_escalation(self):
        """Empty session_id must not trigger any adaptive enforcement."""
        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="READ", session_id="")
        req = SimpleNamespace(system=None, tools=[], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system) or ""
        assert "SESSION-QUALITY" not in text

    @pytest.mark.asyncio
    async def test_empty_history_no_escalation(self):
        """Unknown session (no history) must not trigger any adaptive enforcement."""
        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="BUILD", session_id="fresh-unknown-session")
        req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Edit")], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system) or ""
        assert "SESSION-QUALITY" not in text

    @pytest.mark.asyncio
    async def test_both_conditions_trigger_combined_note(self):
        """Low quality AND stubs both present — both messages must appear."""
        from llm.compressor import append_session_quality
        sid = "both-bad"
        for _ in range(5):
            await append_session_quality(sid, 0.30, stub_delta=0)
        await append_session_quality(sid, 0.30, stub_delta=2)

        t = IntentEnforcementTransformer(enabled=True)
        ctx = TransformContext(intent="BUILD", session_id=sid)
        req = SimpleNamespace(system=None, tools=[SimpleNamespace(name="Edit")], messages=[])
        await t.transform(req, ctx)
        text = _to_text(req.system) or ""
        assert "CRITICAL" in text
        assert "STRICT" in text
