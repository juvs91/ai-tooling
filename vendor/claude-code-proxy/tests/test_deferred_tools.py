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
