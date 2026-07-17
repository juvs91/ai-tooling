# tests/test_deferred_tools.py
"""Tests for DeferredToolsTransformer and extract_deferred_tool_names.

Verifies that CC workflow tools (EnterPlanMode, ExitPlanMode, etc.) are correctly
extracted from CC's request formats and injected into request.tools.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from utils.tool_utils import extract_deferred_tool_names, _CC_WORKFLOW_TOOL_NAMES


# ── extract_deferred_tool_names: system prompt format ────────────────────────

class TestExtractFromSystemPrompt:
    """Tests for the primary <available-deferred-tools> system prompt format."""

    def test_extraction_from_system_string(self):
        """Standard format: <available-deferred-tools> in a plain string system prompt."""
        system = (
            "You are a coding assistant.\n\n"
            "<available-deferred-tools>\n"
            "EnterPlanMode\n"
            "ExitPlanMode\n"
            "TodoWrite\n"
            "AskUserQuestion\n"
            "</available-deferred-tools>\n\n"
            "Additional instructions here."
        )
        result = extract_deferred_tool_names(system)
        assert "EnterPlanMode" in result
        assert "ExitPlanMode" in result
        assert "TodoWrite" in result
        assert "AskUserQuestion" in result

    def test_extraction_from_system_list(self):
        """Structured format: system as list of content blocks."""
        system = [
            {"type": "text", "text": "You are a coding assistant."},
            {
                "type": "text",
                "text": (
                    "<available-deferred-tools>\n"
                    "EnterPlanMode\n"
                    "ExitPlanMode\n"
                    "WebFetch\n"
                    "</available-deferred-tools>"
                ),
            },
        ]
        result = extract_deferred_tool_names(system)
        assert "EnterPlanMode" in result
        assert "ExitPlanMode" in result
        assert "WebFetch" in result

    def test_system_prompt_takes_priority_over_messages(self):
        """When system prompt has <available-deferred-tools>, messages are NOT scanned."""
        system = (
            "<available-deferred-tools>\nEnterPlanMode\n</available-deferred-tools>"
        )
        messages = [
            {
                "role": "user",
                "content": (
                    "<system-reminder>\n"
                    "The following deferred tools are now available via ToolSearch:\n"
                    "ExitPlanMode\n"
                    "TodoWrite\n"
                    "</system-reminder>\n"
                    "Plan the feature."
                ),
            }
        ]
        result = extract_deferred_tool_names(system, messages=messages)
        # System prompt result wins; only EnterPlanMode, not ExitPlanMode/TodoWrite
        assert result == ["EnterPlanMode"]

    def test_empty_system_returns_empty(self):
        """None or empty system returns empty list."""
        assert extract_deferred_tool_names(None) == []
        assert extract_deferred_tool_names("") == []
        assert extract_deferred_tool_names([]) == []

    def test_system_without_block_returns_empty(self):
        """System prompt with no deferred tools block and no messages returns empty."""
        result = extract_deferred_tool_names("You are a helpful assistant.")
        assert result == []


# ── extract_deferred_tool_names: message fallback format ─────────────────────

class TestExtractFromMessageFallback:
    """Tests for the ToolSearch <system-reminder> fallback in user messages."""

    def test_extraction_from_message_toolsearch_format(self):
        """Fallback: ToolSearch system-reminder in last user message."""
        messages = [
            {"role": "user", "content": "Earlier message."},
            {
                "role": "assistant",
                "content": "I can help with that.",
            },
            {
                "role": "user",
                "content": (
                    "<system-reminder>\n"
                    "The following deferred tools are now available via ToolSearch:\n"
                    "EnterPlanMode\n"
                    "ExitPlanMode\n"
                    "AskUserQuestion\n"
                    "TodoWrite\n"
                    "</system-reminder>\n"
                    "Plan the feature."
                ),
            },
        ]
        result = extract_deferred_tool_names(None, messages=messages)
        assert "EnterPlanMode" in result
        assert "ExitPlanMode" in result
        assert "AskUserQuestion" in result
        assert "TodoWrite" in result

    def test_message_content_as_list_of_blocks(self):
        """Fallback works when message content is a list of content blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "<system-reminder>\n"
                            "The following deferred tools are now available via ToolSearch:\n"
                            "EnterPlanMode\n"
                            "ExitPlanMode\n"
                            "</system-reminder>\n"
                        ),
                    },
                    {"type": "text", "text": "Plan the architecture."},
                ],
            }
        ]
        result = extract_deferred_tool_names(None, messages=messages)
        assert "EnterPlanMode" in result
        assert "ExitPlanMode" in result

    def test_filter_unknown_tools_from_messages(self):
        """Fallback only returns tools that are in _CC_WORKFLOW_TOOL_NAMES."""
        messages = [
            {
                "role": "user",
                "content": (
                    "<system-reminder>\n"
                    "The following deferred tools are now available via ToolSearch:\n"
                    "EnterPlanMode\n"
                    "SomeMaliciousTool\n"
                    "ExitPlanMode\n"
                    "AnotherUnknownTool\n"
                    "</system-reminder>\n"
                    "Plan something."
                ),
            }
        ]
        result = extract_deferred_tool_names(None, messages=messages)
        assert "EnterPlanMode" in result
        assert "ExitPlanMode" in result
        assert "SomeMaliciousTool" not in result
        assert "AnotherUnknownTool" not in result

    def test_empty_messages_returns_empty(self):
        """Empty messages list with no system prompt returns empty."""
        assert extract_deferred_tool_names(None, messages=[]) == []
        assert extract_deferred_tool_names(None, messages=None) == []

    def test_no_toolsearch_block_in_messages_returns_empty(self):
        """Messages without a ToolSearch system-reminder return empty."""
        messages = [
            {"role": "user", "content": "Plan the feature without system-reminder."}
        ]
        result = extract_deferred_tool_names(None, messages=messages)
        assert result == []

    def test_scans_in_reverse_order(self):
        """Fallback scans messages in reverse — finds the most recent user message."""
        messages = [
            {
                "role": "user",
                "content": (
                    "<system-reminder>\n"
                    "The following deferred tools are now available via ToolSearch:\n"
                    "TodoWrite\n"
                    "</system-reminder>\n"
                    "Old message."
                ),
            },
            {"role": "assistant", "content": "Working on it."},
            {
                "role": "user",
                "content": (
                    "<system-reminder>\n"
                    "The following deferred tools are now available via ToolSearch:\n"
                    "EnterPlanMode\n"
                    "ExitPlanMode\n"
                    "</system-reminder>\n"
                    "Plan the feature."
                ),
            },
        ]
        result = extract_deferred_tool_names(None, messages=messages)
        # Should find the LAST user message's tools
        assert "EnterPlanMode" in result
        assert "ExitPlanMode" in result


# ── DeferredToolsTransformer integration ─────────────────────────────────────

class TestDeferredToolsTransformer:
    """Integration tests for the DeferredToolsTransformer."""

    def _make_request(self, system=None, messages=None, tools=None):
        req = MagicMock()
        req.system = system
        req.messages = messages or []
        req.tools = tools or []
        return req

    def _make_ctx(self):
        ctx = MagicMock()
        ctx.plan_mode_active = False
        return ctx

    @pytest.mark.asyncio
    async def test_injection_from_system_prompt(self):
        """Transformer injects deferred tools from system prompt into request.tools."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer

        system = (
            "<available-deferred-tools>\n"
            "EnterPlanMode\n"
            "ExitPlanMode\n"
            "</available-deferred-tools>"
        )
        req = self._make_request(system=system, tools=[])
        transformer = DeferredToolsTransformer()
        await transformer.transform(req, self._make_ctx())

        injected_names = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "EnterPlanMode" in injected_names
        assert "ExitPlanMode" in injected_names

    @pytest.mark.asyncio
    async def test_idempotency_already_present_tools_not_duplicated(self):
        """Tools already in request.tools are not injected again."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer

        system = (
            "<available-deferred-tools>\n"
            "EnterPlanMode\n"
            "ExitPlanMode\n"
            "</available-deferred-tools>"
        )
        existing_tool = {
            "name": "EnterPlanMode",
            "description": "Already present",
            "input_schema": {"type": "object", "properties": {}},
        }
        req = self._make_request(system=system, tools=[existing_tool])
        transformer = DeferredToolsTransformer()
        await transformer.transform(req, self._make_ctx())

        enter_plan_count = sum(
            1 for t in req.tools
            if (t.get("name") if isinstance(t, dict) else getattr(t, "name", None)) == "EnterPlanMode"
        )
        assert enter_plan_count == 1  # Not duplicated

    @pytest.mark.asyncio
    async def test_injection_from_message_fallback(self):
        """Transformer injects tools from ToolSearch system-reminder when system prompt has none."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer

        messages = [
            {
                "role": "user",
                "content": (
                    "<system-reminder>\n"
                    "The following deferred tools are now available via ToolSearch:\n"
                    "EnterPlanMode\n"
                    "ExitPlanMode\n"
                    "AskUserQuestion\n"
                    "</system-reminder>\n"
                    "Plan the feature."
                ),
            }
        ]
        req = self._make_request(system=None, messages=messages, tools=[])
        transformer = DeferredToolsTransformer()
        await transformer.transform(req, self._make_ctx())

        injected_names = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "EnterPlanMode" in injected_names
        assert "ExitPlanMode" in injected_names
        assert "AskUserQuestion" in injected_names

    @pytest.mark.asyncio
    async def test_no_injection_when_nothing_found(self):
        """Transformer does not modify tools when no deferred block is found."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer

        existing_tool = {"name": "Read", "description": "Read a file", "input_schema": {}}
        req = self._make_request(system="Plain system prompt.", tools=[existing_tool])
        original_tools = list(req.tools)
        transformer = DeferredToolsTransformer()
        await transformer.transform(req, self._make_ctx())

        assert req.tools == original_tools


# ── _exit_plan_already_called unit tests ─────────────────────────────────────

class TestExitPlanAlreadyCalled:
    """Unit tests for the _exit_plan_already_called helper introduced in Fix 1."""

    def _asst(self, *tool_names):
        """Build a minimal assistant message with tool_use blocks."""
        return {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": name, "id": f"id-{name}", "input": {}}
                for name in tool_names
            ],
        }

    def test_returns_true_when_exit_plan_in_history(self):
        from llm.transformers.deferred_tools import _exit_plan_already_called
        messages = [
            {"role": "user", "content": "Plan it."},
            self._asst("Read"),
            self._asst("ExitPlanMode"),
        ]
        assert _exit_plan_already_called(messages) is True

    def test_returns_false_when_not_in_history(self):
        from llm.transformers.deferred_tools import _exit_plan_already_called
        messages = [
            {"role": "user", "content": "Plan it."},
            self._asst("Read"),
            self._asst("Grep"),
        ]
        assert _exit_plan_already_called(messages) is False

    def test_returns_false_for_empty_and_none(self):
        from llm.transformers.deferred_tools import _exit_plan_already_called
        assert _exit_plan_already_called([]) is False
        assert _exit_plan_already_called(None) is False

    def test_ignores_non_assistant_messages(self):
        """A tool_use in a user message must NOT trigger True."""
        from llm.transformers.deferred_tools import _exit_plan_already_called
        messages = [
            {
                "role": "user",
                "content": [{"type": "tool_use", "name": "ExitPlanMode", "id": "x", "input": {}}],
            }
        ]
        assert _exit_plan_already_called(messages) is False

    def test_finds_exit_plan_in_multi_block_assistant_message(self):
        """Detected when ExitPlanMode is one of several tool_use blocks in a message."""
        from llm.transformers.deferred_tools import _exit_plan_already_called
        messages = [
            self._asst("Read", "Grep", "ExitPlanMode"),
        ]
        assert _exit_plan_already_called(messages) is True

    def test_window_limits_scan(self):
        """ExitPlanMode beyond _EXIT_PLAN_SCAN_WINDOW assistant messages is not found."""
        from llm.transformers.deferred_tools import _exit_plan_already_called, _EXIT_PLAN_SCAN_WINDOW
        # ExitPlanMode oldest, WINDOW+1 Reads after → ExitPlanMode is past the window in reverse
        messages = [self._asst("ExitPlanMode")]
        for _ in range(_EXIT_PLAN_SCAN_WINDOW + 1):
            messages.append(self._asst("Read"))
        assert _exit_plan_already_called(messages) is False

    def test_within_window_is_found(self):
        """ExitPlanMode within window=60 is correctly found."""
        from llm.transformers.deferred_tools import _exit_plan_already_called
        # ExitPlanMode oldest, 50 Reads after → ExitPlanMode is #51 in reversed scan
        messages = [self._asst("ExitPlanMode")]
        for _ in range(50):
            messages.append(self._asst("Read"))
        assert _exit_plan_already_called(messages) is True

    def test_string_content_is_handled_gracefully(self):
        """Assistant message with string content (not list) does not crash."""
        from llm.transformers.deferred_tools import _exit_plan_already_called
        messages = [
            {"role": "assistant", "content": "I will call ExitPlanMode"},
        ]
        # String content → no tool_use blocks → False
        assert _exit_plan_already_called(messages) is False


# ── Multi-turn plan session fix (Bug 1) ──────────────────────────────────────

class TestMultiTurnPlanSession:
    """Regression tests for Bug 1: ExitPlanMode stripped from session cache
    in non-PLAN phases during multi-turn plan sessions.

    Scenario that was broken:
      Turn 1 (PLAN)      → ExitPlanMode injected + saved to cache
      Turn 2 (ANALYZING) → session cache queried, ExitPlanMode stripped ← BUG
      Turn N (ANALYZING) → model ready to call ExitPlanMode, but it's missing
                        → model outputs text instead → plan tab never shows

    Fix: strip ExitPlanMode from cache ONLY after it's been called.
    """

    def _make_request(self, system=None, messages=None, tools=None):
        req = MagicMock()
        req.system = system
        req.messages = messages or []
        req.tools = tools or []
        return req

    def _make_ctx(self, phase="ANALYZING", session_id="test-session-123", plan_mode_active=False):
        ctx = MagicMock()
        ctx.phase = phase
        ctx.session_id = session_id
        ctx.plan_mode_active = plan_mode_active
        return ctx

    def _asst(self, *tool_names):
        return {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": name, "id": f"id-{name}", "input": {}}
                for name in tool_names
            ],
        }

    def _tool_result_user(self):
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "x", "content": "data"}],
        }

    @pytest.mark.asyncio
    async def test_exit_plan_preserved_in_analyzing_before_called(self):
        """CORE BUG FIX: ExitPlanMode must survive in ANALYZING turns when
        the plan session is still in progress (ExitPlanMode not yet called)."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        # Conversation: user asked to plan, model has been reading files
        messages = [
            {"role": "user", "content": "Plan the feature."},
            self._asst("Read"),
            self._tool_result_user(),
            self._asst("Grep"),
            self._tool_result_user(),
        ]
        req = self._make_request(system=None, messages=messages, tools=[])
        ctx = self._make_ctx(phase="ANALYZING")

        # Cache from the earlier PLAN turn
        cached_tools = ["EnterPlanMode", "ExitPlanMode", "TodoWrite", "AskUserQuestion"]

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=cached_tools), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" in injected, (
            "ExitPlanMode was stripped from ANALYZING-phase cache — "
            "plan tab will never show in multi-turn sessions"
        )
        assert "EnterPlanMode" in injected

    @pytest.mark.asyncio
    async def test_exit_plan_stripped_after_called_in_build_phase(self):
        """After ExitPlanMode is called, subsequent BUILD turns must NOT
        re-inject it (plan session is over)."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        # ExitPlanMode IS in the assistant history
        messages = [
            {"role": "user", "content": "Plan the feature."},
            self._asst("Read"),
            self._asst("ExitPlanMode"),   # ← plan submitted
            {"role": "user", "content": "Now implement it."},
        ]
        req = self._make_request(system=None, messages=messages, tools=[])
        ctx = self._make_ctx(phase="BUILD")

        cached_tools = ["EnterPlanMode", "ExitPlanMode", "TodoWrite", "AskUserQuestion"]

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=cached_tools), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" not in injected, (
            "ExitPlanMode must not be re-injected after it was already called"
        )
        assert "EnterPlanMode" not in injected

    @pytest.mark.asyncio
    async def test_exit_plan_preserved_across_multiple_analyzing_turns(self):
        """ExitPlanMode must survive through many consecutive READ/ANALYZING turns."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        # Simulate 8 read turns with no ExitPlanMode call
        messages = [{"role": "user", "content": "Plan the feature."}]
        for _ in range(8):
            messages.append(self._asst("Read"))
            messages.append(self._tool_result_user())

        req = self._make_request(system=None, messages=messages, tools=[])
        ctx = self._make_ctx(phase="READ")

        cached_tools = ["EnterPlanMode", "ExitPlanMode", "AskUserQuestion"]

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=cached_tools), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" in injected

    @pytest.mark.asyncio
    async def test_system_prompt_path_unaffected(self):
        """When <available-deferred-tools> is in system prompt, session cache
        is skipped entirely — this path must behave identically to before."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        system = (
            "<available-deferred-tools>\n"
            "EnterPlanMode\nExitPlanMode\nTodoWrite\n"
            "</available-deferred-tools>"
        )
        req = self._make_request(system=system, messages=[], tools=[])
        ctx = self._make_ctx(phase="ANALYZING")

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock) as mock_get, \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)
            # Cache should NOT be queried when system prompt has the block
            mock_get.assert_not_called()

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" in injected

    @pytest.mark.asyncio
    async def test_plan_phase_guarantee_still_fires_on_new_session(self):
        """PLAN phase guarantee (Step 4) must still inject ExitPlanMode when
        session cache is empty (brand-new session, first PLAN turn)."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        req = self._make_request(system=None, messages=[], tools=[])
        ctx = self._make_ctx(phase="PLAN", plan_mode_active=True)

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" in injected, "PLAN phase guarantee must still fire"
        assert "EnterPlanMode" in injected


# ── Connection reset / session continuity ────────────────────────────────────

class TestConnectionResetScenarios:
    """Tests for the deferred-tools behavior after a CC connection reset or
    session re-take.

    When CC reconnects it either:
    (a) sends the same X-Session-ID → same session cache slot → tools restored
    (b) sends a different/missing X-Session-ID → fresh cache slot →
        PLAN-phase guarantee is the only safety net (non-PLAN phases have none)

    In both cases the proxy creates a brand-new TransformContext per HTTP request,
    so there is no in-request state to "carry over" — only session cache + system
    prompt block matter.
    """

    def _make_request(self, system=None, messages=None, tools=None):
        req = MagicMock()
        req.system = system
        req.messages = messages or []
        req.tools = tools or []
        return req

    def _make_ctx(self, phase="ANALYZING", session_id="session-abc", plan_mode_active=False):
        ctx = MagicMock()
        ctx.phase = phase
        ctx.session_id = session_id
        ctx.plan_mode_active = plan_mode_active
        return ctx

    def _asst(self, *tool_names):
        return {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": n, "id": f"id-{n}", "input": {}}
                for n in tool_names
            ],
        }

    def _tool_result_user(self):
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "x", "content": "data"}],
        }

    # ── (a) Same session_id after reconnect ───────────────────────────────

    @pytest.mark.asyncio
    async def test_same_session_id_restores_tools_after_reconnect(self):
        """Same X-Session-ID after CC reconnect → session cache still has
        ExitPlanMode (not yet called) → available in ANALYZING turn."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        # No system prompt block (newer CC, ToolSearch format only)
        # No user message ToolSearch (tool-result-only turn after reconnect)
        messages = [self._tool_result_user()]
        req = self._make_request(system=None, messages=messages, tools=[])
        ctx = self._make_ctx(phase="ANALYZING", session_id="session-abc")

        # Cache preserved from the previous connection (ExitPlanMode not yet called)
        cached_tools = ["EnterPlanMode", "ExitPlanMode", "AskUserQuestion", "TodoWrite"]

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=cached_tools), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" in injected, (
            "Same session_id should restore ExitPlanMode from cache after reconnect"
        )

    @pytest.mark.asyncio
    async def test_same_session_id_still_strips_after_called(self):
        """Same X-Session-ID reconnect where ExitPlanMode WAS previously called
        — must not re-inject it in BUILD phase."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        messages = [
            {"role": "user", "content": "Plan it."},
            self._asst("ExitPlanMode"),     # ← called before the reconnect
            {"role": "user", "content": "Now implement."},
        ]
        req = self._make_request(system=None, messages=messages, tools=[])
        ctx = self._make_ctx(phase="BUILD", session_id="session-abc")

        cached_tools = ["EnterPlanMode", "ExitPlanMode", "TodoWrite"]

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=cached_tools), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" not in injected
        assert "EnterPlanMode" not in injected

    # ── (b) Different/missing session_id after reconnect ─────────────────

    @pytest.mark.asyncio
    async def test_new_session_id_plan_phase_guarantee_fires(self):
        """Different X-Session-ID + PLAN phase → empty cache → PLAN guarantee
        injects ExitPlanMode so the very first plan turn always has it."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        req = self._make_request(system=None, messages=[], tools=[])
        ctx = self._make_ctx(phase="PLAN", session_id="NEW-session-xyz", plan_mode_active=True)

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" in injected, (
            "PLAN-phase guarantee must inject ExitPlanMode even on fresh session"
        )

    @pytest.mark.asyncio
    async def test_new_session_id_system_prompt_path_always_works(self):
        """Different X-Session-ID + system prompt has <available-deferred-tools>
        → primary path works regardless of session continuity."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        system = (
            "<available-deferred-tools>\n"
            "EnterPlanMode\nExitPlanMode\nTodoWrite\nAskUserQuestion\n"
            "</available-deferred-tools>"
        )
        req = self._make_request(system=system, messages=[], tools=[])
        ctx = self._make_ctx(phase="ANALYZING", session_id="BRAND-NEW-session")

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]) as mock_get, \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)
            mock_get.assert_not_called()   # session cache not needed

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" in injected

    @pytest.mark.asyncio
    async def test_new_session_id_non_plan_phase_no_system_prompt_gap(self):
        """Different X-Session-ID + ANALYZING phase + no system prompt block
        + empty cache → ExitPlanMode NOT available. This is the gap scenario:
        CC reconnected mid-plan-session without X-Session-ID, sending only tool
        results. PLAN guarantee doesn't fire (wrong phase). Document it."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        # Tool-result-only turn, no text from user → no ToolSearch block to parse
        messages = [self._tool_result_user()]
        req = self._make_request(system=None, messages=messages, tools=[])
        ctx = self._make_ctx(phase="ANALYZING", session_id="NEW-unknown-session")

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        # ExitPlanMode is NOT available in this gap scenario.
        # Resolution in practice: CC always sends X-Session-ID so the same
        # session_id is used across reconnects, making the cache the safety net.
        assert "ExitPlanMode" not in injected


# ── Compression / history depth edge cases ───────────────────────────────────

class TestCompressionEdgeCases:
    """Tests for the interaction between context compression and ExitPlanMode
    detection in _exit_plan_already_called (window=60).

    After compression, old messages are replaced with a summary string.
    If ExitPlanMode was in the compressed (old) window, the tool_use block
    is gone from messages — _exit_plan_already_called won't find it.

    Consequence: plan-mode tools are kept in the session cache (false-negative).
    This is acceptable because:
    - Injecting ExitPlanMode into a BUILD turn is harmless (model won't call it)
    - The same window=60 is used by Override G in intent_classifier.py, so the
      classifier also won't force BUILD based on a compressed ExitPlanMode.
    - HAS_WRITES detection (unlimited scan) catches any Write/Edit and
      forces BUILD phase regardless, so plan tools won't trigger plan re-entry.
    """

    def _asst(self, *tool_names):
        return {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": n, "id": f"id-{n}", "input": {}}
                for n in tool_names
            ],
        }

    def _summary_user(self, text="[Compressed summary of prior conversation]"):
        return {"role": "user", "content": text}

    def test_exit_plan_compressed_away_returns_false(self):
        """After compression ExitPlanMode is gone from recent messages →
        _exit_plan_already_called returns False (expected: tools kept in cache).
        This is a documented known limitation, not a crash."""
        from llm.transformers.deferred_tools import _exit_plan_already_called

        # Post-compression layout: summary message + recent reads (no ExitPlanMode)
        messages = [
            self._summary_user(
                "[Summary: user asked to plan feature X. "
                "Model called ExitPlanMode with plan.]"     # ExitPlanMode in TEXT only
            ),
        ]
        for _ in range(10):
            messages.append(self._asst("Read"))

        # ExitPlanMode is only in the summary TEXT, not as a tool_use block
        result = _exit_plan_already_called(messages)
        assert result is False, (
            "ExitPlanMode in summary text (not tool_use block) → False is expected. "
            "Tools will be kept in cache (harmless, documented limitation)."
        )

    def test_exit_plan_just_inside_window_is_found(self):
        """ExitPlanMode at exactly the scan window boundary IS found."""
        from llm.transformers.deferred_tools import _exit_plan_already_called, _EXIT_PLAN_SCAN_WINDOW

        # WINDOW-1 Reads after ExitPlanMode → ExitPlanMode is reached before break
        messages = [self._asst("ExitPlanMode")]
        for _ in range(_EXIT_PLAN_SCAN_WINDOW - 1):
            messages.append(self._asst("Read"))

        assert _exit_plan_already_called(messages) is True

    def test_exit_plan_just_outside_window_is_not_found(self):
        """ExitPlanMode one position beyond the scan window is NOT found."""
        from llm.transformers.deferred_tools import _exit_plan_already_called, _EXIT_PLAN_SCAN_WINDOW

        # WINDOW Reads after ExitPlanMode → count hits WINDOW, break fires before ExitPlanMode
        messages = [self._asst("ExitPlanMode")]
        for _ in range(_EXIT_PLAN_SCAN_WINDOW):
            messages.append(self._asst("Read"))

        assert _exit_plan_already_called(messages) is False

    def test_mixed_user_assistant_messages_count_only_assistant(self):
        """User messages do NOT count toward the window — only assistant messages."""
        from llm.transformers.deferred_tools import _exit_plan_already_called

        # Interleave user and assistant messages.
        # ExitPlanMode is the oldest, 19 assistant Read messages after it,
        # but 50 user messages are interspersed. Only the 19 assistant messages
        # count → ExitPlanMode at position 20 in assistant-message order → found.
        messages = [self._asst("ExitPlanMode")]
        for _ in range(19):
            messages.append({"role": "user", "content": "tool result"})
            messages.append({"role": "user", "content": "tool result 2"})
            messages.append(self._asst("Read"))

        assert _exit_plan_already_called(messages) is True

    @pytest.mark.asyncio
    async def test_transformer_with_compressed_history_keeps_exit_plan_harmlessly(self):
        """After compression, ExitPlanMode is not in tool_use blocks.
        _exit_plan_already_called returns False → tools kept in cache.
        Verify the transformer still injects them (harmless in BUILD phase)."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        # Post-compression: summary + recent reads, ExitPlanMode only in summary text
        messages = [
            {"role": "user", "content": "[Summary: plan was submitted via ExitPlanMode]"},
        ]
        for _ in range(5):
            messages.append({
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Read", "id": "r", "input": {}}],
            })

        req = MagicMock()
        req.system = None
        req.messages = messages
        req.tools = []

        ctx = MagicMock()
        ctx.phase = "BUILD"        # HAS_WRITES detected → BUILD phase
        ctx.session_id = "session-compressed"

        cached_tools = ["EnterPlanMode", "ExitPlanMode", "TodoWrite"]

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=cached_tools), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        # Plan-mode tools are re-injected (false-negative in compressed history).
        # This is harmless: model in BUILD phase won't call ExitPlanMode.
        # Documented known limitation.
        assert "ExitPlanMode" in injected  # kept in cache = injected


# ── RC-8: AskUserQuestion PLAN phase guarantee ───────────────────────────────

class TestPlanDefaultToolsGuarantee:
    """Regression tests for RC-8: some CC project configs (e.g. school-system)
    never include AskUserQuestion in <available-deferred-tools>.

    Fix: _PLAN_DEFAULT_TOOLS = {AskUserQuestion, TodoWrite} are always injected
    when phase == PLAN, regardless of what CC's system prompt contains.
    """

    def _make_request(self, system=None, messages=None, tools=None):
        req = MagicMock()
        req.system = system
        req.messages = messages or []
        req.tools = tools or []
        return req

    def _make_ctx(self, phase="PLAN", session_id="school-session-1", plan_mode_active=False):
        ctx = MagicMock()
        ctx.phase = phase
        ctx.session_id = session_id
        ctx.plan_mode_active = plan_mode_active
        return ctx

    @pytest.mark.asyncio
    async def test_ask_user_question_injected_when_absent_from_system_prompt(self):
        """RC-8 regression: AskUserQuestion must be injected in PLAN phase even
        when CC's system prompt only has EnterPlanMode/ExitPlanMode."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        # School-system CC system prompt: only plan-mode tools, no AskUserQuestion
        system = (
            "<available-deferred-tools>\n"
            "EnterPlanMode\n"
            "ExitPlanMode\n"
            "</available-deferred-tools>"
        )
        req = self._make_request(system=system, tools=[])
        ctx = self._make_ctx(phase="PLAN", plan_mode_active=True)

        with patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "AskUserQuestion" in injected, (
            "AskUserQuestion must be injected in PLAN phase even when absent "
            "from CC's <available-deferred-tools> (RC-8)"
        )
        assert "TodoWrite" in injected, (
            "TodoWrite must also be injected as a PLAN phase default"
        )

    @pytest.mark.asyncio
    async def test_ask_user_question_injected_on_empty_system_prompt(self):
        """RC-8: AskUserQuestion injected via plan guarantee even when system
        prompt has no <available-deferred-tools> block at all (empty cache)."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        req = self._make_request(system=None, messages=[], tools=[])
        ctx = self._make_ctx(phase="PLAN", plan_mode_active=True)

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "AskUserQuestion" in injected
        assert "TodoWrite" in injected
        assert "EnterPlanMode" in injected
        assert "ExitPlanMode" in injected

    @pytest.mark.asyncio
    async def test_ask_user_question_not_duplicated_when_already_in_system_prompt(self):
        """Idempotency: AskUserQuestion already in system prompt → not duplicated."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        system = (
            "<available-deferred-tools>\n"
            "EnterPlanMode\n"
            "ExitPlanMode\n"
            "AskUserQuestion\n"
            "TodoWrite\n"
            "</available-deferred-tools>"
        )
        req = self._make_request(system=system, tools=[])
        ctx = self._make_ctx(phase="PLAN")

        with patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        names = [
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        ]
        assert names.count("AskUserQuestion") == 1, "AskUserQuestion must not be duplicated"
        assert names.count("TodoWrite") == 1, "TodoWrite must not be duplicated"

    @pytest.mark.asyncio
    async def test_ask_user_question_not_injected_outside_plan_phase(self):
        """AskUserQuestion default guarantee only applies to PLAN phase.
        In BUILD phase with empty cache, it must not be injected."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        req = self._make_request(system=None, messages=[], tools=[])
        ctx = self._make_ctx(phase="BUILD")

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        # Empty system prompt + empty cache + non-PLAN phase → no injection at all
        assert req.tools == [], (
            "No tools should be injected in BUILD phase with empty cache and no system prompt"
        )


# ---------------------------------------------------------------------------
# ctx.plan_mode_active as tertiary injection signal (Signal 3 path)
# ---------------------------------------------------------------------------

class TestCtxPlanModeActiveInjection:
    """Regression: DeferredToolsTransformer must use ctx.plan_mode_active as tertiary
    signal for plan tool injection.

    Covers model-initiated plan sessions >60 messages where _plan_mode_active_from_history
    can no longer see EnterPlanMode in the sliding window. ctx.plan_mode_active (set by
    IntentClassifierTransformer via session cache) is the authoritative signal.
    """

    def _make_request(self, system=None, messages=None, tools=None):
        req = MagicMock()
        req.system = system
        req.messages = messages or []
        req.tools = tools or []
        return req

    def _make_ctx(self, phase="EXECUTE", session_id="signal3-test", plan_mode_active=False):
        ctx = MagicMock()
        ctx.phase = phase
        ctx.session_id = session_id
        ctx.plan_mode_active = plan_mode_active
        return ctx

    @pytest.mark.asyncio
    async def test_plan_tools_injected_via_ctx_plan_mode_active(self):
        """ctx.plan_mode_active=True guarantees ExitPlanMode/AskUserQuestion injection
        even when ctx.phase is not PLAN and history scan misses EnterPlanMode.

        Simulates a 70-message session: EnterPlanMode at position 5 is beyond the
        60-message window, so _plan_mode_active_from_history returns False.
        ctx.plan_mode_active=True (from session cache Signal 3) must activate the
        plan tool guarantee.
        """
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        # 70 messages: EnterPlanMode at position 5, beyond the 60-msg window
        messages = []
        for i in range(70):
            if i == 5:
                messages.append({
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "EnterPlanMode", "id": "tu1", "input": {}}],
                })
            else:
                messages.append({
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Read", "id": f"tu{i}",
                                  "input": {"file_path": f"src/file{i}.ts"}}],
                })

        req = self._make_request(system=None, messages=messages, tools=[])
        # phase="EXECUTE", plan_mode_active=True — simulates Signal 3 restoring state
        ctx = self._make_ctx(phase="EXECUTE", plan_mode_active=True)

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        injected = {
            t["name"] if isinstance(t, dict) else getattr(t, "name", None)
            for t in req.tools
        }
        assert "ExitPlanMode" in injected, (
            "ExitPlanMode must be injected when ctx.plan_mode_active=True "
            "even if ctx.phase != PLAN and history scan misses EnterPlanMode"
        )
        assert "AskUserQuestion" in injected, (
            "AskUserQuestion must be injected when ctx.plan_mode_active=True"
        )

    @pytest.mark.asyncio
    async def test_plan_tools_not_injected_when_ctx_plan_mode_false(self):
        """Complement: ctx.plan_mode_active=False + non-PLAN phase → no plan tool guarantee."""
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        from unittest.mock import patch

        req = self._make_request(system=None, messages=[], tools=[])
        ctx = self._make_ctx(phase="EXECUTE", plan_mode_active=False)

        with patch("llm.transformers.deferred_tools.get_session_deferred_tools",
                   new_callable=AsyncMock, return_value=[]), \
             patch("llm.transformers.deferred_tools.save_session_deferred_tools",
                   new_callable=AsyncMock):
            transformer = DeferredToolsTransformer()
            await transformer.transform(req, ctx)

        assert req.tools == [], (
            "No plan tools should be injected when ctx.plan_mode_active=False "
            "and phase is not PLAN"
        )
