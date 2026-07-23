# tests/test_intent_classifier.py
"""Tests for IntentClassifierTransformer and _detect_phase."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from types import SimpleNamespace

from llm.pipeline import TransformContext
from llm.transformers.intent_classifier import (
    IntentClassifierTransformer,
    _detect_phase,
    _detect_analysis_from_history,
    _count_consecutive_reads,
    _resolve_primary_overrides,
    _plan_mode_active,
)
from config import ClassifierConfig, PolicyConfig


def _classifier_cfg(model="", api_key="", base_url=None, timeout=3.0,
                     max_consecutive_errors=3, circuit_reset_seconds=60.0):
    return ClassifierConfig(
        model=model, api_key=api_key, base_url=base_url, timeout=timeout,
        max_consecutive_errors=max_consecutive_errors,
        circuit_reset_seconds=circuit_reset_seconds,
    )


def _policy_cfg(analysis=False, threshold=5):
    return PolicyConfig(
        tool_allowlist_raw="*", policy_note_in_system=True,
        max_input_tokens=0, hard_block_oversize=False,
        analysis_enforcement=analysis, tool_upgrade_threshold=threshold,
        guard_system="",
    )


def _request(text="Hello", tools=None, messages=None):
    if messages is None:
        msg = SimpleNamespace(role="user", content=text)
        messages = [msg]
    return SimpleNamespace(messages=messages, tools=tools)


class TestRegexFallback:
    """When classifier_model is empty or models_differ=False → regex."""

    @pytest.mark.asyncio
    async def test_chat_intent(self):
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("How are you?"), ctx)
        assert ctx.intent == "CHAT"

    @pytest.mark.asyncio
    async def test_building_intent(self):
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("Fix the authentication bug"), ctx)
        assert ctx.intent == "BUILD"

    @pytest.mark.asyncio
    async def test_planning_intent(self):
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("Design the architecture for this system"), ctx)
        assert ctx.intent == "PLAN"

    @pytest.mark.asyncio
    async def test_models_same_skips_llm(self):
        """Even if classifier_model is set, models_differ=False → regex."""
        t = IntentClassifierTransformer(
            _classifier_cfg(model="openai/deepseek-chat", api_key="k"),
            _policy_cfg(), models_differ=False,
        )
        ctx = TransformContext()
        with patch("llm.transformers.intent_classifier.classify_intent") as mock:
            await t.transform(_request("hello"), ctx)
            mock.assert_not_called()
        assert ctx.intent == "CHAT"


class TestLLMClassifier:
    """When classifier_model is set AND models_differ=True → LLM."""

    @pytest.mark.asyncio
    async def test_llm_called(self):
        t = IntentClassifierTransformer(
            _classifier_cfg(model="openai/deepseek-chat", api_key="k", base_url="http://x", timeout=2.0),
            _policy_cfg(), models_differ=True,
        )
        ctx = TransformContext()
        with patch("llm.transformers.intent_classifier.classify_intent", new_callable=AsyncMock, return_value=("BUILD", 1.0, None)) as mock:
            await t.transform(_request("implement the feature"), ctx)
            mock.assert_called_once()
            assert ctx.intent == "BUILD"


class TestDetectPhase:
    """_detect_phase() returns (HAS_WRITES | READS_ONLY | None, [tool_names])."""

    def test_no_messages_returns_none(self):
        phase, tools = _detect_phase([])
        assert phase is None
        assert tools == []
        phase2, tools2 = _detect_phase(None)
        assert phase2 is None
        assert tools2 == []

    def test_no_tool_use_returns_none(self):
        msgs = [SimpleNamespace(role="assistant", content="just text")]
        phase, tools = _detect_phase(msgs)
        assert phase is None
        assert tools == []

    def test_write_tool_returns_has_writes(self):
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
        ]
        phase, tools = _detect_phase(msgs)
        assert phase == "HAS_WRITES"
        assert "Read" in tools and "Edit" in tools

    def test_read_only_returns_reads_only(self):
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Grep"),
            ]),
        ]
        phase, tools = _detect_phase(msgs)
        assert phase == "READS_ONLY"
        assert "Read" in tools and "Grep" in tools

    def test_dict_messages(self):
        """Works with dict-based messages (from JSON)."""
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Write"},
            ]},
        ]
        phase, tools = _detect_phase(msgs)
        assert phase == "HAS_WRITES"
        assert tools == ["Write"]

    def test_skips_user_messages(self):
        msgs = [
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_use", name="Write"),
            ]),
        ]
        phase, tools = _detect_phase(msgs)
        assert phase is None
        assert tools == []

    def test_finds_write_beyond_5_tools(self):
        """Scans ALL messages — Write anywhere in history returns HAS_WRITES.

        Old behavior capped at 5 tools, causing the Write from planning to scroll
        out of the window during GLM-4.7's multi-read implementation preamble.
        New behavior: return immediately on the first Write found (no window cap).
        """
        msgs = [
            # Older message with Write — MUST be reached even after 5+ recent reads
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Write"),
            ]),
            # Recent message with 5 reads (processed first by reversed())
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Grep"),
                SimpleNamespace(type="tool_use", name="Glob"),
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
        ]
        phase, tools = _detect_phase(msgs)
        assert phase == "HAS_WRITES"
        # tool_context list is capped at 5 for display, but detection found Write
        assert len(tools) <= 5


class TestPhaseInTransform:
    """Phase detection in transform() combines history + text classification."""

    @pytest.mark.asyncio
    async def test_no_history_chat_becomes_explore(self):
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("Hello"), ctx)
        assert ctx.intent == "CHAT"
        assert ctx.phase == "EXPLORE"

    @pytest.mark.asyncio
    async def test_no_history_planning_becomes_plan(self):
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("Design the architecture for this system"), ctx)
        assert ctx.intent == "PLAN"
        assert ctx.phase == "PLAN"

    @pytest.mark.asyncio
    async def test_no_history_building_becomes_execute(self):
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("Fix the authentication bug"), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_history_writes_chat_overrides_to_building(self):
        """Agentic routing: CHAT intent after writes → BUILDING (any write = active execution)."""
        msgs = [
            SimpleNamespace(role="user", content="Hello"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content="How are you?"),
        ]
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_history_reads_with_planning_text_becomes_plan(self):
        """Read tools + PLANNING text → PLAN."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Grep"),
            ]),
            SimpleNamespace(role="user", content="Design the architecture for this"),
        ]
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request(messages=msgs), ctx)
        assert ctx.phase == "PLAN"

    @pytest.mark.asyncio
    async def test_history_reads_with_chat_text_becomes_explore(self):
        """Read tools + CHAT text → EXPLORE."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content="What does this function do?"),
        ]
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request(messages=msgs), ctx)
        assert ctx.phase == "EXPLORE"


class TestOverrideCAnalysisFallbackToAnalyzing:
    """Override C: analysis_detected + classifier missed → ANALYZING (was Override 3 → PLANNING)."""

    @pytest.mark.asyncio
    async def test_mixed_analysis_building_becomes_building(self):
        """Mixed keywords: 'Lee exhaustivamente... Propón un fix...'
        BUILDING_RE matches 'fix'+'bug', ANALYSIS_RE matches 'exhaustiv'.
        _is_explicit_analysis=False (has building keywords) → BUILDING wins.
        CC phase: user wants to ACT (fix), not just Gather (analyze)."""
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(
            _request("Lee exhaustivamente todos los archivos. Haz un análisis arquitectónico. Identifica los 3 bugs más críticos. Propón un fix para cada uno."),
            ctx,
        )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_mixed_analysis_building_reads_only_becomes_building(self):
        """Mixed keywords with read-only history → BUILDING (user wants to fix, not just analyze)."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Grep"),
            ]),
            SimpleNamespace(role="user", content="Fix all bugs in the codebase. Be thorough and exhaustive."),
        ]
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_building_analysis_with_writes_stays_building(self):
        """Analysis + HAS_WRITES → Override 3 does NOT fire (mid-execution guard)."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content="Now analyze the codebase exhaustively and fix the bugs"),
        ]
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_building_non_analysis_stays_building(self):
        """Pure BUILDING (no analysis keywords) → Override 3 does NOT fire."""
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("Fix the authentication bug in server.py"), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.is_analysis is False


class TestAnalysisDetection:

    @pytest.mark.asyncio
    async def test_analysis_detected(self):
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(analysis=True), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("Analyze the codebase exhaustively"), ctx)
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_analysis_detected_even_when_enforcement_disabled(self):
        """Detection is always active for routing; enforcement only controls guardrails."""
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(analysis=False), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("Analyze the codebase exhaustively"), ctx)
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_non_analysis_request(self):
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(analysis=True), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request("Hello, how are you?"), ctx)
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_analysis_propagated_from_history(self):
        """Analysis detected from earlier user message, not just last."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="x", content="file contents..."),
            ]),
        ]
        t = IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)
        ctx = TransformContext()
        await t.transform(_request(messages=msgs), ctx)
        assert ctx.is_analysis is True


class TestDetectAnalysisFromHistory:
    def test_finds_analysis_in_earlier_message(self):
        msgs = [
            SimpleNamespace(role="user", content="Analyze the system exhaustively"),
            SimpleNamespace(role="assistant", content="Sure."),
            SimpleNamespace(role="user", content="continue"),
        ]
        assert _detect_analysis_from_history(msgs) is True

    def test_no_analysis_in_history(self):
        msgs = [
            SimpleNamespace(role="user", content="Fix the bug"),
            SimpleNamespace(role="user", content="yes do it"),
        ]
        assert _detect_analysis_from_history(msgs) is False

    def test_empty_messages(self):
        assert _detect_analysis_from_history([]) is False
        assert _detect_analysis_from_history(None) is False

    def test_caps_at_10_user_messages(self):
        """Only checks last 10 user messages to avoid sticky analysis detection."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
        ] + [
            SimpleNamespace(role="user", content="ok")
            for _ in range(11)
        ]
        # Analysis request is 12 user messages back → beyond 10-message limit
        assert _detect_analysis_from_history(msgs) is False

    def test_within_10_user_messages(self):
        """Analysis request within 10 user messages is detected."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
        ] + [
            SimpleNamespace(role="user", content="ok")
            for _ in range(8)
        ]
        # Analysis request is 9 user messages back → within limit
        assert _detect_analysis_from_history(msgs) is True

    def test_dict_messages(self):
        msgs = [
            {"role": "user", "content": "Review the code comprehensively"},
            {"role": "user", "content": "thanks"},
        ]
        assert _detect_analysis_from_history(msgs) is True


# ──────────────────────────────────────────────────────────────────────
# E2E: Count Consecutive Reads
# ──────────────────────────────────────────────────────────────────────

class TestCountConsecutiveReads:
    """_count_consecutive_reads counts read-only assistant turns from the end."""

    def test_empty(self):
        assert _count_consecutive_reads([]) == 0
        assert _count_consecutive_reads(None) == 0

    def test_no_assistant_tool_use(self):
        msgs = [SimpleNamespace(role="assistant", content="just text")]
        assert _count_consecutive_reads(msgs) == 0

    def test_single_read_turn(self):
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
        ]
        assert _count_consecutive_reads(msgs) == 1

    def test_multiple_read_turns(self):
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content="continue"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Grep"),
            ]),
            SimpleNamespace(role="user", content="continue"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Glob"),
            ]),
        ]
        assert _count_consecutive_reads(msgs) == 3

    def test_stops_at_write(self):
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content="ok"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content="ok"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
        ]
        # Reversed: Read(1) → Edit(stop) — only 1 consecutive read from end
        assert _count_consecutive_reads(msgs) == 1

    def test_many_reads_for_synthesize_fallback(self):
        """Simulates 16 consecutive reads — should trigger Override D (>= 15)."""
        msgs = []
        for i in range(16):
            msgs.append(SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]))
            msgs.append(SimpleNamespace(role="user", content="continue"))
        assert _count_consecutive_reads(msgs) == 16


# ──────────────────────────────────────────────────────────────────────
# E2E: Five-Intent Classification Matrix
# ──────────────────────────────────────────────────────────────────────

class TestFiveIntentMapping:
    """Verify all 5 intents map to correct phase, analysis_phase, and is_analysis."""

    def _make_transformer(self, synth_fallback=15):
        return IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
            synth_reads_fallback=synth_fallback,
        )

    @pytest.mark.asyncio
    async def test_chat_maps_to_explore(self):
        """CHAT → phase=EXPLORE, analysis_phase=NONE, is_analysis=False."""
        ctx = TransformContext()
        await self._make_transformer().transform(_request("Hello there!"), ctx)
        assert ctx.intent == "CHAT"
        assert ctx.phase == "EXPLORE"
        assert ctx.analysis_phase == "NONE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_planning_maps_to_plan(self):
        """PLANNING → phase=PLAN, analysis_phase=NONE, is_analysis=False."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Design the architecture for the new microservice"), ctx,
        )
        assert ctx.intent == "PLAN"
        assert ctx.phase == "PLAN"
        assert ctx.analysis_phase == "NONE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_building_maps_to_execute(self):
        """BUILDING → phase=EXECUTE, analysis_phase=NONE, is_analysis=False."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Fix the authentication bug in server.py"), ctx,
        )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.analysis_phase == "NONE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_analyzing_maps_to_plan_with_analysis(self):
        """ANALYZING → phase=PLAN, analysis_phase=ANALYZING, is_analysis=True.
        Triggered by analysis keywords (Override C catches regex CHAT/BUILDING→ANALYZING)."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Analyze the codebase exhaustively"), ctx,
        )
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.analysis_phase == "READ"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_synthesizing_via_override_d(self):
        """SYNTHESIZING → phase=PLAN, analysis_phase=SYNTHESIZING, is_analysis=True.
        Triggered by Override D when consecutive reads >= synth_fallback.
        Note: uses synth_fallback=5 to stay within the 10-user-message lookback
        window of _detect_analysis_from_history."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
        ]
        for _ in range(6):
            msgs.append(SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]))
            msgs.append(SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="x", content="file contents..."),
            ]))

        ctx = TransformContext()
        await self._make_transformer(synth_fallback=5).transform(
            _request(messages=msgs), ctx,
        )
        assert ctx.intent == "SYNTHESIZING"
        assert ctx.phase == "PLAN"
        assert ctx.analysis_phase == "SYNTHESIZING"
        assert ctx.is_analysis is True


# ──────────────────────────────────────────────────────────────────────
# E2E: Override A — HAS_WRITES trumps everything → BUILDING
# ──────────────────────────────────────────────────────────────────────

class TestOverrideA_HasWritesForcesBuilding:
    """Override A: any write in tool history → BUILDING/EXECUTE, regardless of text intent."""

    def _make_transformer(self):
        return IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
        )

    def _msgs_with_writes(self, user_text):
        """Helper: assistant wrote files, then user sends text."""
        return [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content=user_text),
        ]

    @pytest.mark.asyncio
    async def test_chat_after_writes_becomes_building(self):
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request(messages=self._msgs_with_writes("How are you?")), ctx,
        )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_planning_after_writes_becomes_building(self):
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request(messages=self._msgs_with_writes("Design the architecture")), ctx,
        )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_analysis_after_writes_becomes_building_with_done(self):
        """Explicit analysis request + HAS_WRITES → ANALYZING (build→analysis pivot).

        Override A is narrowed: _is_explicit_analysis bypasses it.
        Override C1 fires: pure analysis intent ignores HAS_WRITES.
        """
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request(messages=self._msgs_with_writes("Analyze the codebase exhaustively")), ctx,
        )
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True
        assert ctx.analysis_phase == "READ"

    @pytest.mark.asyncio
    async def test_building_after_writes_stays_building(self):
        """BUILDING + writes → stays BUILDING (no override needed)."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request(messages=self._msgs_with_writes("Fix the bug")), ctx,
        )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"


# ──────────────────────────────────────────────────────────────────────
# E2E: Override B — CHAT + 3+ read tools → BUILDING
# ──────────────────────────────────────────────────────────────────────

class TestOverrideB_ChatWithToolActivity:
    """Override B: CHAT intent + READS_ONLY + >= 3 tools → BUILDING."""

    def _make_transformer(self):
        return IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
        )

    @pytest.mark.asyncio
    async def test_chat_with_3_read_tools_becomes_building(self):
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Grep"),
                SimpleNamespace(type="tool_use", name="Glob"),
            ]),
            SimpleNamespace(role="user", content="What is this?"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_chat_with_2_read_tools_stays_chat(self):
        """< 3 tools → Override B does NOT fire."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Grep"),
            ]),
            SimpleNamespace(role="user", content="What is this?"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        # CHAT stays CHAT (only 2 tools, below threshold)
        assert ctx.intent == "CHAT"
        assert ctx.phase == "EXPLORE"

    @pytest.mark.asyncio
    async def test_chat_with_write_tools_uses_override_a_not_b(self):
        """HAS_WRITES + CHAT → Override A fires (not B)."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Grep"),
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content="What is this?"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"


# ──────────────────────────────────────────────────────────────────────
# E2E: Override C — analysis_detected + classifier missed → ANALYZING
# ──────────────────────────────────────────────────────────────────────

class TestOverrideC_AnalysisFallback:
    """Override C: analysis_detected=True but classifier returned non-analysis intent → ANALYZING."""

    def _make_transformer(self):
        return IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
        )

    @pytest.mark.asyncio
    async def test_chat_analysis_no_history_becomes_analyzing(self):
        """Regex returns CHAT (no building/planning keywords), but ANALYSIS_RE matches → ANALYZING."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Haz un análisis exhaustivo del codebase"), ctx,
        )
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_planning_analysis_no_history_becomes_analyzing(self):
        """Regex returns PLANNING, but ANALYSIS_RE matches → Override C → ANALYZING."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Analyze the system architecture comprehensively"), ctx,
        )
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_mixed_analysis_building_no_history_stays_building(self):
        """Mixed keywords: 'Fix all bugs exhaustively' — BUILDING_RE matches 'fix'+'bug',
        ANALYSIS_RE matches 'exhaustiv'. _is_explicit_analysis=False → C1 doesn't fire.
        Not tool_result → C2 doesn't fire. User wants to FIX → BUILDING."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Fix all bugs exhaustively in the codebase"), ctx,
        )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_analysis_from_history_with_tool_result_last(self):
        """Last message is tool_result, but analysis request in history → Override C fires."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="x", content="file contents..."),
            ]),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True
        assert ctx.analysis_phase == "READ"

    @pytest.mark.asyncio
    async def test_override_c_does_not_fire_with_writes(self):
        """analysis_detected=True + HAS_WRITES → Override A wins, not C."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content="continue"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.analysis_phase == "NONE"


# ──────────────────────────────────────────────────────────────────────
# E2E: Override D — ANALYZING + reads >= threshold → SYNTHESIZING
# ──────────────────────────────────────────────────────────────────────

class TestOverrideD_SynthesizingFallback:
    """Override D: ANALYZING + consecutive_reads >= synth_fallback → SYNTHESIZING."""

    def _build_read_history(self, n_reads, analysis_text="Analyze exhaustively"):
        """Build history with N consecutive read turns + analysis request."""
        msgs = [SimpleNamespace(role="user", content=analysis_text)]
        for _ in range(n_reads):
            msgs.append(SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]))
            msgs.append(SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="x", content="contents"),
            ]))
        return msgs

    @pytest.mark.asyncio
    async def test_below_threshold_stays_analyzing(self):
        """4 reads < 5 threshold → stays ANALYZING.
        Uses small threshold to stay within _detect_analysis_from_history's 10-user-message lookback."""
        msgs = self._build_read_history(4)
        ctx = TransformContext()
        t = IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
            synth_reads_fallback=5,
        )
        await t.transform(_request(messages=msgs), ctx)
        assert ctx.intent == "READ"
        assert ctx.analysis_phase == "READ"

    @pytest.mark.asyncio
    async def test_at_threshold_triggers_synthesizing(self):
        """5 reads >= 5 threshold → Override D fires → SYNTHESIZING."""
        msgs = self._build_read_history(5)
        ctx = TransformContext()
        t = IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
            synth_reads_fallback=5,
        )
        await t.transform(_request(messages=msgs), ctx)
        assert ctx.intent == "SYNTHESIZING"
        assert ctx.analysis_phase == "SYNTHESIZING"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_above_threshold_triggers_synthesizing(self):
        """8 reads >= 7 threshold → Override D fires → SYNTHESIZING."""
        msgs = self._build_read_history(8)
        ctx = TransformContext()
        t = IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
            synth_reads_fallback=7,
        )
        await t.transform(_request(messages=msgs), ctx)
        assert ctx.intent == "SYNTHESIZING"
        assert ctx.analysis_phase == "SYNTHESIZING"

    @pytest.mark.asyncio
    async def test_override_d_does_not_fire_without_analysis(self):
        """Many reads but no analysis_detected → no Override D."""
        msgs = []
        for _ in range(20):
            msgs.append(SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]))
            msgs.append(SimpleNamespace(role="user", content="continue"))
        ctx = TransformContext()
        t = IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
            synth_reads_fallback=5,
        )
        await t.transform(_request(messages=msgs), ctx)
        # No analysis_detected → intent stays whatever regex returns (CHAT for "continue")
        # Override B might fire (CHAT + 5 read tools) → BUILDING
        assert ctx.intent in ("CHAT", "BUILD")
        assert ctx.analysis_phase == "NONE"
        assert ctx.is_analysis is False


# ──────────────────────────────────────────────────────────────────────
# E2E: LLM Classifier returns ANALYZING/SYNTHESIZING
# ──────────────────────────────────────────────────────────────────────

class TestLLMClassifierFiveIntents:
    """When LLM classifier is active and returns ANALYZING/SYNTHESIZING."""

    def _make_llm_transformer(self, synth_fallback=15):
        return IntentClassifierTransformer(
            _classifier_cfg(model="openai/deepseek-chat", api_key="k", base_url="http://x"),
            _policy_cfg(), models_differ=True,
            synth_reads_fallback=synth_fallback,
        )

    @pytest.mark.asyncio
    async def test_llm_returns_analyzing(self):
        """LLM returns ANALYZING → analysis_phase=ANALYZING, phase=PLAN."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="x", content="file..."),
            ]),
        ]
        ctx = TransformContext()
        with patch(
            "llm.transformers.intent_classifier.classify_intent",
            new_callable=AsyncMock, return_value=("READ", 0.9, None),
        ):
            await self._make_llm_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.analysis_phase == "READ"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_llm_returns_synthesizing(self):
        """LLM returns SYNTHESIZING → analysis_phase=SYNTHESIZING, phase=PLAN."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
        ]
        # Add 10 read turns
        for _ in range(10):
            msgs.append(SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]))
            msgs.append(SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="x", content="file..."),
            ]))
        # Add a final assistant message with no domain tools — this represents
        # the model having processed all tool results and being ready to synthesize.
        # Without this, Override F fires (last assistant had Read → converts SYNTHESIZING→READ).
        msgs.append(SimpleNamespace(role="assistant", content="I now have sufficient context."))

        ctx = TransformContext()
        with patch(
            "llm.transformers.intent_classifier.classify_intent",
            new_callable=AsyncMock, return_value=("SYNTHESIZING", 0.9, None),
        ):
            await self._make_llm_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "SYNTHESIZING"
        assert ctx.phase == "PLAN"
        assert ctx.analysis_phase == "SYNTHESIZING"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_llm_returns_building_mixed_keywords_stays_building(self):
        """LLM returns BUILDING, text has mixed analysis+building keywords.
        _is_explicit_analysis=False → C1 doesn't fire. Not tool_result → C2 doesn't fire.
        CC phase: user wants to ACT (fix bugs), LLM decision respected."""
        ctx = TransformContext()
        with patch(
            "llm.transformers.intent_classifier.classify_intent",
            new_callable=AsyncMock, return_value=("BUILD", 0.9, None),
        ):
            await self._make_llm_transformer().transform(
                _request("Analyze the codebase exhaustively and fix bugs"), ctx,
            )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_llm_returns_chat_no_analysis_stays_chat(self):
        """LLM returns CHAT, no analysis detected → stays CHAT."""
        ctx = TransformContext()
        with patch(
            "llm.transformers.intent_classifier.classify_intent",
            new_callable=AsyncMock, return_value=("CHAT", 0.95, None),
        ):
            await self._make_llm_transformer().transform(
                _request("Hello, how are you?"), ctx,
            )
        assert ctx.intent == "CHAT"
        assert ctx.phase == "EXPLORE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_llm_returns_analyzing_with_writes_override_a(self):
        """LLM returns ANALYZING but HAS_WRITES → Override A → BUILDING."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content="continue"),
        ]
        ctx = TransformContext()
        with patch(
            "llm.transformers.intent_classifier.classify_intent",
            new_callable=AsyncMock, return_value=("READ", 0.9, None),
        ):
            await self._make_llm_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.analysis_phase == "NONE"
        assert ctx.is_analysis is False


# ──────────────────────────────────────────────────────────────────────
# E2E: Full CC Session Simulations
# ──────────────────────────────────────────────────────────────────────

class TestFullSessionSimulations:
    """Simulate realistic Claude Code conversation patterns."""

    def _make_transformer(self, synth_fallback=15):
        return IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
            synth_reads_fallback=synth_fallback,
        )

    @pytest.mark.asyncio
    async def test_session_analysis_gather_phase(self):
        """Turn 3 of analysis: user asked for analysis, agent read 2 files, user sends tool_result.
        Should be ANALYZING (Override C catches)."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the proxy codebase exhaustively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Grep"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="a", content="server.py contents"),
                SimpleNamespace(type="tool_result", tool_use_id="b", content="grep results"),
            ]),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True
        assert ctx.analysis_read_count >= 1

    @pytest.mark.asyncio
    async def test_session_analysis_to_building_transition(self):
        """Agent analyzed, then started writing → switches to BUILDING."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase and fix the bugs"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="a", content="file..."),
            ]),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="b", content="ok"),
            ]),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.analysis_phase == "DONE"

    @pytest.mark.asyncio
    async def test_session_pure_building_no_analysis(self):
        """Normal building session: user asks to fix, agent reads + edits."""
        msgs = [
            SimpleNamespace(role="user", content="Fix the authentication bug in server.py"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="a", content="file..."),
            ]),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="b", content="ok"),
            ]),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_session_chat_greeting(self):
        """Simple greeting — no tools, no analysis."""
        ctx = TransformContext()
        await self._make_transformer().transform(_request("Hi! Can you help me?"), ctx)
        assert ctx.intent == "CHAT"
        assert ctx.phase == "EXPLORE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_session_spanish_analysis(self):
        """Spanish: 'Lee exhaustivamente todos los archivos del proxy'."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Lee exhaustivamente todos los archivos del proxy y analiza la arquitectura"), ctx,
        )
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_session_spanish_building(self):
        """Spanish building request without analysis keywords."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Arregla el error de login en server.py"), ctx,
        )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_session_spanish_planning(self):
        """Spanish planning request. Uses 'plan' which matches PLANNING_RE as exact word."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Haz un plan de la arquitectura para el nuevo microservicio"), ctx,
        )
        assert ctx.intent == "PLAN"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_session_continue_during_analysis_reads(self):
        """User says 'continue' during analysis gather phase → ANALYZING (via history detection)."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the proxy codebase comprehensively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="a", content="file..."),
            ]),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Grep"),
            ]),
            SimpleNamespace(role="user", content="continue"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_session_synthesize_after_many_reads(self):
        """After reads >= threshold in analysis session → SYNTHESIZING.
        Uses synth_fallback=5 to stay within 10-user-message lookback window."""
        msgs = [SimpleNamespace(role="user", content="Analyze the codebase exhaustively")]
        for _ in range(6):
            msgs.append(SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]))
            msgs.append(SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="x", content="contents"),
            ]))
        ctx = TransformContext()
        await self._make_transformer(synth_fallback=5).transform(
            _request(messages=msgs), ctx,
        )
        assert ctx.intent == "SYNTHESIZING"
        assert ctx.analysis_phase == "SYNTHESIZING"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_session_mixed_deep_think_analysis(self):
        """'Think deeply about the architecture' — matches ANALYSIS_RE."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Think deeply about the system architecture and identify problems"), ctx,
        )
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_analysis_read_count_tracked(self):
        """Verify analysis_read_count is populated correctly."""
        msgs = [SimpleNamespace(role="user", content="Analyze the codebase exhaustively")]
        for _ in range(7):
            msgs.append(SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]))
            msgs.append(SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="x", content="file"),
            ]))
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.analysis_read_count == 7

    @pytest.mark.asyncio
    async def test_non_analysis_read_count_zero(self):
        """Non-analysis sessions don't count reads."""
        msgs = [
            SimpleNamespace(role="user", content="Fix the bug"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="x", content="file"),
            ]),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.analysis_read_count == 0


class TestCCPhaseAwareClassification:
    """Tests for CC Gather-Act-Verify phase-aware classification.

    Validates that the classifier correctly maps CC phases:
    - Gather: ANALYZING (pure analysis) or continuation (tool_results, "continue")
    - Act: BUILDING (write/implement intent)
    - Verify: BUILDING (post-write testing)
    - Plan: PLANNING (strategy without analysis)
    - Explore: CHAT (simple questions)
    """

    def _make_transformer(self, synth_fallback=15):
        return IntentClassifierTransformer(
            _classifier_cfg(), _policy_cfg(), models_differ=False,
            synth_reads_fallback=synth_fallback,
        )

    # --- Gather phase: explicit analysis entry ---

    @pytest.mark.asyncio
    async def test_gather_pure_analysis_request(self):
        """Pure analysis text (no building keywords) → ANALYZING via C1."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Lee exhaustivamente todos los archivos del proxy"), ctx,
        )
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True
        assert ctx.analysis_phase == "READ"

    @pytest.mark.asyncio
    async def test_gather_analysis_pivot_from_building(self):
        """Pure analysis request with HAS_WRITES → ANALYZING (C1 pivot).
        User can pivot from Act to Gather phase at any time."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content="Ahora analiza exhaustivamente todo el codebase"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True

    # --- Gather continuation: tool_results and short text ---

    @pytest.mark.asyncio
    async def test_gather_continuation_tool_result(self):
        """Pure tool_result during analysis session → ANALYZING (C2).
        Agent is still in Gather loop reading files."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="a", content="file contents"),
            ]),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "READ"
        assert ctx.phase == "PLAN"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_gather_continuation_short_yes(self):
        """'yes' during analysis session → ANALYZING (short ambiguous continuation)."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the proxy codebase comprehensively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="a", content="file..."),
            ]),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Grep"),
            ]),
            SimpleNamespace(role="user", content="yes"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "READ"
        assert ctx.is_analysis is True

    @pytest.mark.asyncio
    async def test_gather_continuation_si(self):
        """'sí' during analysis session → ANALYZING (short ambiguous continuation)."""
        msgs = [
            SimpleNamespace(role="user", content="Analiza exhaustivamente el sistema"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content="sí"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "READ"
        assert ctx.is_analysis is True

    # --- Gather→Act transition: new text with building intent ---

    @pytest.mark.asyncio
    async def test_gather_to_act_new_building_text(self):
        """New text with building intent during analysis session → BUILDING.
        'Explora los tests' is a building/explore task, not analysis continuation."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="a", content="file contents"),
            ]),
            SimpleNamespace(role="user", content="Now implement the fix for the auth bug"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_gather_to_act_mixed_keywords(self):
        """Mixed analysis+building keywords → BUILDING (user wants to ACT).
        C1 doesn't fire (_is_explicit_analysis=False), C2 doesn't fire (text, not tool_result)."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("Analyze exhaustively and fix all the bugs"), ctx,
        )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.is_analysis is False

    # --- Act phase: HAS_WRITES override ---

    @pytest.mark.asyncio
    async def test_act_phase_has_writes_forces_building(self):
        """HAS_WRITES + non-analysis text → BUILDING (Override A).
        Agent is in Act phase, 'continue' means keep writing."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Write"),
            ]),
            SimpleNamespace(role="user", content="continue"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_act_phase_has_writes_chat_becomes_building(self):
        """HAS_WRITES + 'what is this?' → BUILDING (Override A).
        Even CHAT is overridden to BUILDING during Act phase."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content="What is this?"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    # --- Context injection: no bias for non-analysis messages ---

    @pytest.mark.asyncio
    async def test_no_analysis_context_for_building_text_in_analysis_session(self):
        """Building text during analysis session → no 'ANALYSIS SESSION ACTIVE' injected.
        Prevents classifier bias. The classifier decides by CONTENT, not session history."""
        msgs = [
            SimpleNamespace(role="user", content="Analyze the codebase exhaustively"),
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
            ]),
            SimpleNamespace(role="user", content="Now deploy the fix to production"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        # Should be BUILDING (deploy/fix keywords), not ANALYZING
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    # --- Verify phase: post-write tool results ---

    @pytest.mark.asyncio
    async def test_verify_phase_tool_result_after_writes(self):
        """Tool result after writes → BUILDING (Override A, verify phase)."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Edit"),
            ]),
            SimpleNamespace(role="user", content=[
                SimpleNamespace(type="tool_result", tool_use_id="a", content="test output"),
            ]),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    # --- Short text without analysis session → no override ---

    @pytest.mark.asyncio
    async def test_short_text_no_analysis_session_stays_chat(self):
        """'continue' without analysis session → CHAT (no override).
        Short ambiguous continuation only applies IN analysis sessions."""
        ctx = TransformContext()
        await self._make_transformer().transform(
            _request("continue"), ctx,
        )
        assert ctx.intent == "CHAT"
        assert ctx.phase == "EXPLORE"
        assert ctx.is_analysis is False

    @pytest.mark.asyncio
    async def test_short_text_with_reads_no_analysis_stays_building(self):
        """'yes' with read history but NO analysis session → BUILDING (Override B).
        Short text only triggers Gather continuation when analysis_detected=True."""
        msgs = [
            SimpleNamespace(role="assistant", content=[
                SimpleNamespace(type="tool_use", name="Read"),
                SimpleNamespace(type="tool_use", name="Grep"),
                SimpleNamespace(type="tool_use", name="Glob"),
            ]),
            SimpleNamespace(role="user", content="yes"),
        ]
        ctx = TransformContext()
        await self._make_transformer().transform(_request(messages=msgs), ctx)
        # Override B: CHAT + READS_ONLY + 3 tools → BUILDING
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.is_analysis is False


class TestResolvePrimaryOverrides:
    """Unit tests for _resolve_primary_overrides() — pure function, explicit priority."""

    def _msgs_with_tool(self, name: str):
        # Use dict format — matches production (CC messages come as JSON)
        # _get_recent_all_tools only handles dict blocks (unlike _detect_phase which handles both)
        return [{"role": "assistant", "content": [{"type": "tool_use", "name": name}]}]

    def test_no_override_returns_none(self):
        """Returns None when no override condition matches."""
        result = _resolve_primary_overrides(
            ctx_intent="BUILD",
            history_phase="READS_ONLY",
            _is_explicit_analysis=False,
            analysis_detected=False,
            _is_gather_continuation=False,
            tool_names=[],
            consecutive_reads=0,
            messages=[],
        )
        assert result is None

    def test_p1_explicit_analysis_fires(self):
        """P1: explicit analysis → READ (highest priority)."""
        result = _resolve_primary_overrides(
            ctx_intent="READ",  # already READ — should NOT fire (guard: not in READ/SYNTH)
            history_phase="HAS_WRITES",
            _is_explicit_analysis=True,
            analysis_detected=True,
            _is_gather_continuation=False,
            tool_names=[],
            consecutive_reads=3,
            messages=[],
        )
        assert result is None  # intent already READ → guard blocks it

        result = _resolve_primary_overrides(
            ctx_intent="BUILD",
            history_phase="HAS_WRITES",
            _is_explicit_analysis=True,
            analysis_detected=True,
            _is_gather_continuation=False,
            tool_names=[],
            consecutive_reads=3,
            messages=[],
        )
        assert result is not None
        assert result.name == "C1"
        assert result.intent == "READ"
        assert result.analysis_read_count == 3

    def test_p1_beats_exit_plan_mode(self):
        """P1 fires before P2 — explicit analysis overrides ExitPlanMode."""
        msgs = self._msgs_with_tool("ExitPlanMode")
        result = _resolve_primary_overrides(
            ctx_intent="BUILD",
            history_phase="HAS_WRITES",
            _is_explicit_analysis=True,
            analysis_detected=True,
            _is_gather_continuation=False,
            tool_names=[],
            consecutive_reads=2,
            messages=msgs,
        )
        assert result is not None
        assert result.name == "C1"  # P1 won, not G

    def test_p2_exit_plan_mode_fires(self):
        """P2: ExitPlanMode in history → BUILD."""
        msgs = self._msgs_with_tool("ExitPlanMode")
        result = _resolve_primary_overrides(
            ctx_intent="READ",
            history_phase="READS_ONLY",
            _is_explicit_analysis=False,
            analysis_detected=False,
            _is_gather_continuation=False,
            tool_names=[],
            consecutive_reads=0,
            messages=msgs,
        )
        assert result is not None
        assert result.name == "G"
        assert result.intent == "BUILD"
        assert result.is_analysis is False

    def test_p2_skipped_if_intent_already_build(self):
        """P2 guard: ctx_intent == BUILD → G does not fire."""
        msgs = self._msgs_with_tool("ExitPlanMode")
        result = _resolve_primary_overrides(
            ctx_intent="BUILD",
            history_phase="READS_ONLY",
            _is_explicit_analysis=False,
            analysis_detected=False,
            _is_gather_continuation=False,
            tool_names=[],
            consecutive_reads=0,
            messages=msgs,
        )
        assert result is None

    def test_p3_has_writes_fires(self):
        """P3: HAS_WRITES → BUILD (Override A equivalent)."""
        result = _resolve_primary_overrides(
            ctx_intent="READ",
            history_phase="HAS_WRITES",
            _is_explicit_analysis=False,
            analysis_detected=False,
            _is_gather_continuation=False,
            tool_names=["Read"],
            consecutive_reads=1,
            messages=[],
        )
        assert result is not None
        assert result.name == "A"
        assert result.intent == "BUILD"

    def test_p4_gather_continuation_fires(self):
        """P4: gather continuation → READ (Override C2 equivalent)."""
        result = _resolve_primary_overrides(
            ctx_intent="BUILD",
            history_phase="READS_ONLY",
            _is_explicit_analysis=False,
            analysis_detected=True,
            _is_gather_continuation=True,
            tool_names=["Read", "Grep"],
            consecutive_reads=4,
            messages=[],
        )
        assert result is not None
        assert result.name == "C2"
        assert result.intent == "READ"
        assert result.analysis_read_count == 4

    def test_p4_blocked_by_has_writes(self):
        """P4 guard: HAS_WRITES → C2 does NOT fire (prevents stall)."""
        result = _resolve_primary_overrides(
            ctx_intent="BUILD",
            history_phase="HAS_WRITES",
            _is_explicit_analysis=False,
            analysis_detected=True,
            _is_gather_continuation=True,
            tool_names=["Read"],
            consecutive_reads=6,
            messages=[],
        )
        # P3 would fire before P4 (HAS_WRITES + not BUILD) — but intent is already BUILD
        # so P3 guard fails. P4 guard (HAS_WRITES) also blocks C2. Result: None.
        assert result is None

    def test_p5_chat_with_reads_fires(self):
        """P5: CHAT + READS_ONLY + 3 tools → BUILD (Override B equivalent)."""
        result = _resolve_primary_overrides(
            ctx_intent="CHAT",
            history_phase="READS_ONLY",
            _is_explicit_analysis=False,
            analysis_detected=False,
            _is_gather_continuation=False,
            tool_names=["Read", "Grep", "Glob"],
            consecutive_reads=0,
            messages=[],
        )
        assert result is not None
        assert result.name == "B"
        assert result.intent == "BUILD"

    def test_p5_requires_3_tools(self):
        """P5: fewer than 3 tools → does NOT fire."""
        result = _resolve_primary_overrides(
            ctx_intent="CHAT",
            history_phase="READS_ONLY",
            _is_explicit_analysis=False,
            analysis_detected=False,
            _is_gather_continuation=False,
            tool_names=["Read", "Grep"],
            consecutive_reads=0,
            messages=[],
        )
        assert result is None

    def test_priority_p1_beats_p3(self):
        """P1 (explicit analysis) fires before P3 (HAS_WRITES) even with writes in history."""
        result = _resolve_primary_overrides(
            ctx_intent="BUILD",
            history_phase="HAS_WRITES",
            _is_explicit_analysis=True,
            analysis_detected=True,
            _is_gather_continuation=False,
            tool_names=[],
            consecutive_reads=2,
            messages=[],
        )
        assert result is not None
        assert result.name == "C1"  # P1, not A

    def test_priority_p2_beats_p3(self):
        """P2 (ExitPlanMode) fires before P3 (HAS_WRITES)."""
        msgs = self._msgs_with_tool("ExitPlanMode")
        result = _resolve_primary_overrides(
            ctx_intent="READ",
            history_phase="HAS_WRITES",
            _is_explicit_analysis=False,
            analysis_detected=False,
            _is_gather_continuation=False,
            tool_names=[],
            consecutive_reads=0,
            messages=msgs,
        )
        assert result is not None
        assert result.name == "G"  # P2 won, not A

    # ── P0 (PLAN_LOCK) regression tests ────────────────────────────────────────

    def _msgs_with_enter_plan_mode(self):
        """History with EnterPlanMode tool call but no ExitPlanMode — plan mode active."""
        return [{"role": "assistant", "content": [{"type": "tool_use", "name": "EnterPlanMode"}]}]

    def _msgs_with_full_plan_cycle(self):
        """History with EnterPlanMode followed by ExitPlanMode — plan mode ended."""
        return [
            {"role": "assistant", "content": [{"type": "tool_use", "name": "EnterPlanMode"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "ExitPlanMode"}]},
        ]

    def test_p0_plan_lock_fires_for_build_intent(self):
        """P0: plan_mode_active=True blocks Override A (BUILD intent + HAS_WRITES)."""
        result = _resolve_primary_overrides(
            ctx_intent="BUILD",
            history_phase="HAS_WRITES",
            _is_explicit_analysis=False,
            analysis_detected=False,
            _is_gather_continuation=False,
            tool_names=["Write"],
            consecutive_reads=0,
            messages=[],
            plan_mode_active=True,
        )
        assert result is not None
        assert result.name == "PLAN_LOCK"
        assert result.intent == "PLAN"
        assert result.phase == "PLAN"
        assert result.is_analysis is False

    def test_p0_plan_lock_fires_for_chat_and_read_intents(self):
        """P0 blocks C2 (CHAT/READ + gather continuation) when plan mode is active."""
        for intent in ("CHAT", "READ"):
            result = _resolve_primary_overrides(
                ctx_intent=intent,
                history_phase="READS_ONLY",
                _is_explicit_analysis=False,
                analysis_detected=True,
                _is_gather_continuation=True,
                tool_names=["Read", "Grep"],
                consecutive_reads=5,
                messages=[],
                plan_mode_active=True,
            )
            assert result is not None and result.name == "PLAN_LOCK", (
                f"Expected PLAN_LOCK for intent={intent}, got {result}"
            )
            assert result.analysis_read_count == 5  # preserved for nudge

    def test_p0_does_not_fire_after_exit_plan_mode(self):
        """P0 inactive once ExitPlanMode called — P2/Override G can fire normally."""
        msgs = self._msgs_with_full_plan_cycle()
        # _plan_mode_active() returns False because ExitPlanMode resets found_enter
        assert _plan_mode_active(msgs) is False

        result = _resolve_primary_overrides(
            ctx_intent="READ",
            history_phase="READS_ONLY",
            _is_explicit_analysis=False,
            analysis_detected=False,
            _is_gather_continuation=False,
            tool_names=[],
            consecutive_reads=0,
            messages=msgs,
            plan_mode_active=False,  # plan mode ended
        )
        # P2 (Override G) fires because ExitPlanMode is in history
        assert result is not None
        assert result.name == "G"
        assert result.intent == "BUILD"

    def test_p0_blocks_c2_regression(self):
        """Regression: old C2 scenario (analysis_detected + gather continuation + PLAN intent)
        must NOT override to READ when plan mode is active."""
        result = _resolve_primary_overrides(
            ctx_intent="PLAN",
            history_phase="READS_ONLY",
            _is_explicit_analysis=False,
            analysis_detected=True,   # user requested analysis as part of planning
            _is_gather_continuation=True,  # current message is pure tool_result
            tool_names=["Read", "Grep", "Bash"],
            consecutive_reads=8,
            messages=[],
            plan_mode_active=True,
        )
        assert result is not None
        assert result.name == "PLAN_LOCK"   # P0 wins, NOT C2
        assert result.intent == "PLAN"      # stays PLAN, not overridden to READ
        assert result.is_analysis is False  # no READ enforcement


# ── Ralph mode detection (Item 1) ────────────────────────────────────────────

class TestRalphModeDetection:
    """PROXY_SESSION_MODE: ralph in system prompt must set ctx.ralph_mode = True."""

    def _make_transformer(self):
        return IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)

    def _make_request_with_system(self, text="fix the bug", system=""):
        msg = SimpleNamespace(role="user", content=text)
        req = SimpleNamespace(messages=[msg], tools=None, system=system)
        return req

    @pytest.mark.asyncio
    async def test_ralph_marker_sets_ralph_mode_true(self):
        """The PROXY_SESSION_MODE: ralph marker must set ctx.ralph_mode = True."""
        t = self._make_transformer()
        ctx = TransformContext()
        req = self._make_request_with_system(
            system="You are helpful.\nPROXY_SESSION_MODE: ralph"
        )
        await t.transform(req, ctx)
        assert ctx.ralph_mode is True

    @pytest.mark.asyncio
    async def test_no_marker_leaves_ralph_mode_false(self):
        """Without the marker, ralph_mode must remain False."""
        t = self._make_transformer()
        ctx = TransformContext()
        req = self._make_request_with_system(system="You are helpful.")
        await t.transform(req, ctx)
        assert ctx.ralph_mode is False

    @pytest.mark.asyncio
    async def test_empty_system_leaves_ralph_mode_false(self):
        t = self._make_transformer()
        ctx = TransformContext()
        req = self._make_request_with_system(system="")
        await t.transform(req, ctx)
        assert ctx.ralph_mode is False

    @pytest.mark.asyncio
    async def test_none_system_leaves_ralph_mode_false(self):
        t = self._make_transformer()
        ctx = TransformContext()
        req = SimpleNamespace(
            messages=[SimpleNamespace(role="user", content="fix the bug")],
            tools=None,
            system=None,
        )
        await t.transform(req, ctx)
        assert ctx.ralph_mode is False

    @pytest.mark.asyncio
    async def test_ralph_and_plan_mode_coexist(self):
        """Ralph mode and plan mode can both be active in the same turn."""
        t = self._make_transformer()
        ctx = TransformContext()
        req = self._make_request_with_system(
            text="write the plan",
            system="Plan mode is active.\nPROXY_SESSION_MODE: ralph",
        )
        await t.transform(req, ctx)
        assert ctx.ralph_mode is True
        assert ctx.plan_mode_active is True

    @pytest.mark.asyncio
    async def test_partial_marker_does_not_trigger(self):
        """A partial match (e.g. just 'ralph') must NOT set ralph_mode."""
        t = self._make_transformer()
        ctx = TransformContext()
        req = self._make_request_with_system(system="ralph session mode")
        await t.transform(req, ctx)
        assert ctx.ralph_mode is False

    @pytest.mark.asyncio
    async def test_ralph_mode_default_is_false(self):
        """TransformContext must default ralph_mode to False."""
        ctx = TransformContext()
        assert ctx.ralph_mode is False


# ── Signal 4: Implicit ExitPlanMode vía CC UI mode change (ADR-0008) ─────────

class TestSignal4ImplicitExitPlanMode:
    """Signal 4: when CC UI switches /plan → Autoedit/Bypass, unblock PLAN_LOCK.

    Setup: EnterPlanMode in message history (Signal 0 active) but no
    "Plan mode is active" in system prompt (Signal 1 absent) + BUILD intent.
    Expected: plan_mode_active=False, intent=BUILD, phase=EXECUTE.
    """

    def _make_transformer(self):
        return IntentClassifierTransformer(_classifier_cfg(), _policy_cfg(), models_differ=False)

    def _make_request(self, text, system="", extra_msgs=None):
        """Build request with EnterPlanMode in history + optional system prompt."""
        # History: EnterPlanMode was called (Signal 0 → plan_mode_active=True)
        history = [
            {"role": "user", "content": "Diseña la arquitectura del proxy"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "EnterPlanMode"},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "server.py"}},
            ]},
        ]
        if extra_msgs:
            history.extend(extra_msgs)
        history.append({"role": "user", "content": text})
        return SimpleNamespace(messages=history, tools=None, system=system)

    # ── Core: Signal 4 fires (unblock) ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_signal4_fires_build_intent_no_cc_plan_mode(self):
        """Signal 4: EnterPlanMode in history + no CC /plan + BUILD → plan unlocked."""
        t = self._make_transformer()
        ctx = TransformContext()
        req = self._make_request("Implementa el fix ahora", system="")
        await t.transform(req, ctx)
        assert ctx.plan_mode_active is False, "Signal 4 debe limpiar plan_mode_active"
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    @pytest.mark.asyncio
    async def test_signal4_fires_spanish_implementation(self):
        """Signal 4: 'arregla el bug' después de salir de /plan → BUILD."""
        t = self._make_transformer()
        ctx = TransformContext()
        req = self._make_request("Arregla el bug de autenticación en server.py", system="")
        await t.transform(req, ctx)
        assert ctx.plan_mode_active is False
        assert ctx.intent == "BUILD"

    @pytest.mark.asyncio
    async def test_signal4_fires_with_writes_in_history(self):
        """Signal 4 fires even when plan.md was written (HAS_WRITES from plan file)."""
        t = self._make_transformer()
        ctx = TransformContext()
        # Plan session: model wrote the plan file, then user switches to Autoedit
        extra = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": "plan.md"}},
            ]},
        ]
        # Use explicit BUILD-only text (avoid "plan" keyword which triggers PLANNING_RE)
        req = self._make_request("Arregla el bug de autenticación ahora", system="", extra_msgs=extra)
        await t.transform(req, ctx)
        assert ctx.plan_mode_active is False
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    # ── Signal 4 does NOT fire (lock stays) ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_signal4_blocked_when_cc_still_in_plan_mode(self):
        """Signal 4 does NOT fire when CC injects 'Plan mode is active' (still in /plan)."""
        t = self._make_transformer()
        ctx = TransformContext()
        # System prompt still has "Plan mode is active" → CC is in /plan mode
        req = self._make_request(
            "implementa el fix",
            system="Plan mode is active. Write your plan to the plan file.",
        )
        await t.transform(req, ctx)
        # P0 PLAN_LOCK should still fire because Signal 1 activates plan_mode_active
        assert ctx.plan_mode_active is True
        assert ctx.intent == "PLAN"

    @pytest.mark.asyncio
    async def test_signal4_blocked_for_plan_intent(self):
        """Signal 4 does NOT fire for PLAN intent (model still planning, not implementing)."""
        t = self._make_transformer()
        ctx = TransformContext()
        req = self._make_request("Diseña la arquitectura del nuevo módulo", system="")
        await t.transform(req, ctx)
        # PLAN intent → P0 PLAN_LOCK fires, plan_mode_active stays True
        assert ctx.plan_mode_active is True
        assert ctx.intent == "PLAN"

    @pytest.mark.asyncio
    async def test_signal4_blocked_for_chat_intent(self):
        """Signal 4 does NOT fire for CHAT intent (ambiguous, not clear BUILD request)."""
        t = self._make_transformer()
        ctx = TransformContext()
        req = self._make_request("¿Cuántos archivos tiene el proxy?", system="")
        await t.transform(req, ctx)
        # CHAT intent → P0 PLAN_LOCK stays
        assert ctx.plan_mode_active is True
        assert ctx.intent == "PLAN"

    @pytest.mark.asyncio
    async def test_signal4_not_needed_when_exit_plan_mode_called(self):
        """ExitPlanMode in history already clears lock — Signal 4 is a fallback."""
        t = self._make_transformer()
        ctx = TransformContext()
        # History: Enter + Exit plan mode, then user requests implementation
        history = [
            {"role": "user", "content": "Diseña la arquitectura"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "EnterPlanMode"},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "ExitPlanMode"},
            ]},
            {"role": "user", "content": "Implementa la solución"},
        ]
        req = SimpleNamespace(messages=history, tools=None, system="")
        await t.transform(req, ctx)
        # ExitPlanMode clears lock before Signal 4 is even checked
        assert ctx.plan_mode_active is False
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"

    # ── Full CC session simulation: /plan → Autoedit → implement ────────────

    @pytest.mark.asyncio
    async def test_full_session_plan_mode_to_autoedit(self):
        """Full session: user enters /plan, model plans, user switches to Autoedit, implements.

        Simulates the exact scenario reported for Kimi K2:
        Turn 1 (CC in /plan): EnterPlanMode called, plan written
        Turn 2 (CC switched to Autoedit): user says "implementa ahora"
        Expected: plan_mode_active=False, intent=BUILD, phase=EXECUTE
        """
        t = self._make_transformer()
        ctx = TransformContext()

        # Turn 2 — user switched CC to Autoedit (no "Plan mode is active" in system)
        history = [
            # Turn 1: CC in /plan mode (user started planning session)
            {"role": "user", "content": "Diseña el fix para el P0 PLAN_LOCK"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "EnterPlanMode"},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "intent_classifier.py"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "r1", "content": "file contents..."},
            ]},
            {"role": "assistant", "content": [
                # Model wrote the plan file but forgot to call ExitPlanMode (Kimi K2 bug)
                {"type": "tool_use", "name": "Write", "input": {"file_path": "plan.md"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "w1", "content": "ok"},
            ]},
            # Turn 2: user switched CC to Autoedit and types implementation request
            {"role": "user", "content": "Implementa el fix ahora por favor"},
        ]
        # No "Plan mode is active" in system — CC is in Autoedit mode
        req = SimpleNamespace(messages=history, tools=None, system="")
        await t.transform(req, ctx)

        assert ctx.plan_mode_active is False, (
            "Signal 4 debe limpiar plan_mode_active cuando CC cambia a Autoedit + intent=BUILD"
        )
        assert ctx.intent == "BUILD"
        assert ctx.phase == "EXECUTE"
        assert ctx.is_analysis is False

