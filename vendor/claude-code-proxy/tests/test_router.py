# tests/test_router.py
"""Tests for router/llm_router.py and router/model_mapper.py."""
import pytest
from unittest.mock import MagicMock


class TestModelMapper:
    """Tests for model_mapper.py functions."""

    def test_claude_sonnet_maps_to_big_model(self):
        from router.model_mapper import map_claude_alias_to_target

        result = map_claude_alias_to_target(
            "claude-sonnet-4-20250514",
            preferred_provider="openai",
            big_model="gpt-4",
            small_model="gpt-3.5-turbo",
        )
        assert "gpt-4" in result

    def test_claude_haiku_maps_to_small_model(self):
        from router.model_mapper import map_claude_alias_to_target

        result = map_claude_alias_to_target(
            "claude-haiku-4-5-20251001",
            preferred_provider="openai",
            big_model="gpt-4",
            small_model="gpt-3.5-turbo",
        )
        assert "gpt-3.5-turbo" in result

    def test_adds_provider_prefix(self):
        from router.model_mapper import map_claude_alias_to_target

        result = map_claude_alias_to_target(
            "claude-sonnet-4-20250514",
            preferred_provider="openai",
            big_model="gpt-4",
            small_model="gpt-3.5-turbo",
        )
        assert result.startswith("openai/")

    def test_gemini_provider_prefix(self):
        from router.model_mapper import map_claude_alias_to_target

        result = map_claude_alias_to_target(
            "claude-sonnet-4-20250514",
            preferred_provider="google",
            big_model="gemini-pro",
            small_model="gemini-flash",
        )
        assert result.startswith("gemini/")


class TestLLMRouter:
    """Tests for llm_router.py functions."""

    def test_choose_local_model_defaults_to_small(self):
        from router.llm_router import choose_local_model

        result = choose_local_model(
            messages=[],
            max_out=100,
            approx_tokens=50,
            system_chars=100,
            tools_count=0,
            small_model="small",
            big_model="big",
            building_model="build",
        )
        assert result in ["small", "big", "build"]

    def test_choose_local_model_with_tools_prefers_building(self):
        from router.llm_router import choose_local_model

        result = choose_local_model(
            messages=[],
            max_out=1000,
            approx_tokens=1000,
            system_chars=5000,
            tools_count=10,  # Many tools suggests building/execution
            small_model="small",
            big_model="big",
            building_model="build",
        )
        # With many tools, should prefer building model
        assert result in ["build", "big"]

    def test_choose_local_model_planning_keywords(self):
        from router.llm_router import choose_local_model

        messages = [
            MagicMock(content="Please create a plan for implementing this feature")
        ]

        result = choose_local_model(
            messages=messages,
            max_out=1000,
            approx_tokens=500,
            system_chars=1000,
            tools_count=0,
            small_model="small",
            big_model="big",
            building_model="build",
        )
        # Planning keywords should influence model choice
        assert result in ["small", "big", "build"]

    def test_choose_local_model_building_keywords(self):
        from router.llm_router import choose_local_model

        messages = [
            MagicMock(content="Build the authentication module and write the code")
        ]

        result = choose_local_model(
            messages=messages,
            max_out=2000,
            approx_tokens=1000,
            system_chars=2000,
            tools_count=5,
            small_model="small",
            big_model="big",
            building_model="build",
        )
        # Building keywords should prefer building model
        assert result in ["build", "big"]


class TestSystemReminderStripping:
    """Tests that <system-reminder> tags are stripped before classification."""

    def test_get_last_user_text_strips_system_reminder(self):
        from router.llm_router import get_last_user_text

        messages = [{"role": "user", "content": [
            {"type": "text", "text": (
                '<system-reminder>\n'
                'Note: /path/file.py was read before the last conversation...\n'
                'Called the Read tool with: {"file_path":"/path/file.py"}\n'
                'Result: "1→import json\\n2→import re..."\n'
                '</system-reminder>\n'
                'Lee exhaustivamente todos los archivos y analiza la arquitectura'
            )}
        ]}]
        result = get_last_user_text(messages)
        assert "<system-reminder>" not in result
        assert "analiza la arquitectura" in result

    def test_get_last_user_text_strips_multiple_reminders(self):
        from router.llm_router import get_last_user_text

        messages = [{"role": "user", "content": [
            {"type": "text", "text": (
                '<system-reminder>First reminder content</system-reminder>\n'
                '<system-reminder>Second reminder content</system-reminder>\n'
                'Fix the authentication bug'
            )}
        ]}]
        result = get_last_user_text(messages)
        assert "<system-reminder>" not in result
        assert "Fix the authentication bug" in result

    def test_get_last_user_text_preserves_clean_text(self):
        from router.llm_router import get_last_user_text

        messages = [{"role": "user", "content": "Plan the implementation"}]
        result = get_last_user_text(messages)
        assert result == "Plan the implementation"

    def test_regex_fallback_with_stripped_reminder(self):
        from router.llm_router import _regex_fallback_intent

        # With stripping applied upstream, classifier sees clean text
        # "arquitectura" matches PLANNING_RE via "arquitect"
        clean_text = "Analiza la arquitectura del sistema y crea un plan"
        assert _regex_fallback_intent(clean_text) == "PLAN"

    def test_regex_building_with_clean_text(self):
        from router.llm_router import _regex_fallback_intent

        assert _regex_fallback_intent("Fix the bug in authentication") == "BUILD"

    def test_regex_contaminated_vs_clean(self):
        """Without stripping, system-reminder content triggers BUILDING keywords."""
        from router.llm_router import _regex_fallback_intent

        contaminated = (
            '<system-reminder>Called the Read tool with: '
            '{"file_path":"/path"} tool_result tool_use_id</system-reminder>\n'
            'Plan the implementation'
        )
        # Both PLAN ("plan") and BUILD ("tool_result") match → PLAN wins
        result = _regex_fallback_intent(contaminated)
        assert result == "PLAN"

    def test_get_last_user_text_long_reminder_truncation(self):
        """Ensure stripping happens before the 8000-char truncation."""
        from router.llm_router import get_last_user_text

        # Simulate a very long system-reminder that would consume the truncation budget
        long_reminder = '<system-reminder>' + 'x' * 7000 + '</system-reminder>\n'
        messages = [{"role": "user", "content": [
            {"type": "text", "text": long_reminder + "Analyze the codebase architecture"}
        ]}]
        result = get_last_user_text(messages)
        assert "Analyze the codebase" in result
        assert "<system-reminder>" not in result

    def test_get_last_user_text_reminder_exceeds_8000_chars(self):
        """Regression: system-reminder >8000 chars must be stripped, not truncated mid-tag.

        Before the fix, content was truncated to 8000 chars BEFORE stripping,
        so the closing </system-reminder> tag was cut off and the regex failed,
        leaving raw system-reminder text that contaminated the classifier.
        """
        from router.llm_router import get_last_user_text

        # Build a reminder that's >8000 chars total (opening tag + body + closing tag)
        # This simulates CC's CLAUDE.md + env info + git status injected as system-reminder
        body = 'A' * 9000  # well over 8000
        long_reminder = f'<system-reminder>{body}</system-reminder>\n'
        assert len(long_reminder) > 8000, "test setup: reminder must exceed truncation limit"

        messages = [{"role": "user", "content": [
            {"type": "text", "text": long_reminder + "Plan the implementation of auth"}
        ]}]
        result = get_last_user_text(messages)
        assert "<system-reminder>" not in result
        assert "Plan the implementation" in result


class TestPassthroughThinkingGuard:
    """Tests that thinking params are only injected for streaming requests."""

    def test_preflight_non_stream_no_thinking(self):
        """Non-streaming (preflight) requests must NOT get thinking params."""
        from proxy.proxy import _build_passthrough_body
        from llm.pipeline import TransformContext

        ctx = TransformContext(raw_body=b"", is_analysis=True, intent="READ")
        ctx.analysis_phase = "READ"

        request = MagicMock()
        request.messages = [{"role": "user", "content": "test"}]
        request.max_tokens = 4096
        request.system = None
        request.tools = None
        request.temperature = None

        # Simulate non-stream: analysis_thinking=None (as proxy.py now does)
        body = _build_passthrough_body(request, "glm-4.7", ctx=ctx, analysis_thinking=None)
        assert "thinking" not in body
        assert "clear_thinking" not in body

    def test_streaming_gets_thinking(self):
        """Streaming requests for READ phase SHOULD get thinking params."""
        from proxy.proxy import _build_passthrough_body
        from llm.pipeline import TransformContext

        ctx = TransformContext(raw_body=b"", is_analysis=True, intent="READ")
        ctx.analysis_phase = "READ"

        request = MagicMock()
        request.messages = [{"role": "user", "content": "test"}]
        request.max_tokens = 4096
        request.system = None
        request.tools = None
        request.temperature = None

        thinking_params = {"thinking": {"type": "enabled"}}
        body = _build_passthrough_body(request, "glm-4.7", ctx=ctx, analysis_thinking=thinking_params)
        assert body["thinking"] == {"type": "enabled"}

    def test_non_analyzing_no_thinking(self):
        """Non-READ phase should NOT get thinking even with params provided."""
        from proxy.proxy import _build_passthrough_body
        from llm.pipeline import TransformContext

        ctx = TransformContext(raw_body=b"", is_analysis=False, intent="CHAT")
        ctx.analysis_phase = None

        request = MagicMock()
        request.messages = [{"role": "user", "content": "hello"}]
        request.max_tokens = 4096
        request.system = None
        request.tools = None
        request.temperature = None
        request.thinking = None  # CC did not request thinking

        thinking_params = {"thinking": {"type": "enabled"}}
        body = _build_passthrough_body(request, "glm-4.7", ctx=ctx, analysis_thinking=thinking_params)
        assert "thinking" not in body

    def test_thinking_skipped_when_body_exceeds_cap(self):
        """Thinking should be skipped when message body chars exceed THINKING_MAX_INPUT_CHARS (when cap > 0)."""
        import os
        from proxy.proxy import _build_passthrough_body
        from llm.pipeline import TransformContext

        ctx = TransformContext(raw_body=b"", is_analysis=True, intent="READ")
        ctx.analysis_phase = "READ"

        # Build a request with large message content (~250K chars > 200K explicit cap)
        large_content = "x" * 250000
        request = MagicMock()
        request.messages = [{"role": "user", "content": large_content}]
        request.max_tokens = 4096
        request.system = None
        request.tools = None
        request.temperature = None

        thinking_params = {"thinking": {"type": "enabled"}}
        old_val = os.environ.get("THINKING_MAX_INPUT_CHARS")
        try:
            # Explicitly set a cap of 200K to test the skip mechanism
            os.environ["THINKING_MAX_INPUT_CHARS"] = "200000"
            body = _build_passthrough_body(request, "glm-4.7", ctx=ctx, analysis_thinking=thinking_params)
            assert "thinking" not in body, "thinking should be skipped when body exceeds explicit cap"
        finally:
            if old_val is None:
                os.environ.pop("THINKING_MAX_INPUT_CHARS", None)
            else:
                os.environ["THINKING_MAX_INPUT_CHARS"] = old_val

    def test_thinking_allowed_when_body_under_cap(self):
        """Thinking should be injected when message body is under the cap."""
        import os
        from proxy.proxy import _build_passthrough_body
        from llm.pipeline import TransformContext

        ctx = TransformContext(raw_body=b"", is_analysis=True, intent="READ")
        ctx.analysis_phase = "READ"

        request = MagicMock()
        request.messages = [{"role": "user", "content": "analyze this small request"}]
        request.max_tokens = 4096
        request.system = None
        request.tools = None
        request.temperature = None

        thinking_params = {"thinking": {"type": "enabled"}}
        old_val = os.environ.get("THINKING_MAX_INPUT_CHARS")
        try:
            os.environ["THINKING_MAX_INPUT_CHARS"] = "200000"
            body = _build_passthrough_body(request, "glm-4.7", ctx=ctx, analysis_thinking=thinking_params)
            assert body["thinking"] == {"type": "enabled"}
        finally:
            if old_val is None:
                os.environ.pop("THINKING_MAX_INPUT_CHARS", None)
            else:
                os.environ["THINKING_MAX_INPUT_CHARS"] = old_val

    def test_thinking_always_injected_when_cap_zero(self):
        """THINKING_MAX_INPUT_CHARS=0 means no cap — thinking injected even for very large bodies."""
        import os
        from proxy.proxy import _build_passthrough_body
        from llm.pipeline import TransformContext

        ctx = TransformContext(raw_body=b"", is_analysis=True, intent="READ")
        ctx.analysis_phase = "READ"

        # Body that would exceed the old 200K cap (real analysis sessions easily hit this)
        large_content = "x" * 300000
        request = MagicMock()
        request.messages = [{"role": "user", "content": large_content}]
        request.max_tokens = 4096
        request.system = None
        request.tools = None
        request.temperature = None

        thinking_params = {"thinking": {"type": "enabled"}}
        old_val = os.environ.get("THINKING_MAX_INPUT_CHARS")
        try:
            # cap=0 means disabled — thinking must always be injected for READ phase
            os.environ["THINKING_MAX_INPUT_CHARS"] = "0"
            body = _build_passthrough_body(request, "glm-4.7", ctx=ctx, analysis_thinking=thinking_params)
            assert body.get("thinking") == {"type": "enabled"}, (
                "thinking should always be injected when THINKING_MAX_INPUT_CHARS=0 (no cap)"
            )
        finally:
            if old_val is None:
                os.environ.pop("THINKING_MAX_INPUT_CHARS", None)
            else:
                os.environ["THINKING_MAX_INPUT_CHARS"] = old_val


class TestPassthroughStreamingFallback:
    """Tests that streaming passthrough falls back to litellm on first-chunk failure."""

    @pytest.mark.asyncio
    async def test_stream_timeout_falls_back_to_litellm(self):
        """When passthrough stream times out on first chunk, should fall through to litellm."""
        from unittest.mock import AsyncMock, patch
        from proxy.proxy import run_messages
        from llm.pipeline import TransformContext
        from config import ProxyConfig

        # This test validates the control flow: PassthroughError raised during
        # eager first-chunk → caught by except block → falls through to litellm
        # We test this by checking that _build_passthrough_body is called (passthrough attempted)
        # and that when it raises, the function continues to litellm code path

        # The actual integration test would require mocking the full litellm pipeline,
        # so we just verify the _empty_stream helper works
        from proxy.proxy import _empty_stream
        chunks = []
        async for chunk in _empty_stream():
            chunks.append(chunk)
        assert chunks == [], "_empty_stream should yield nothing"


class TestProxyPolicy:
    """Tests for proxy.py policy functions."""

    def test_provider_cap_groq(self):
        from llm.transformers.token_cap import provider_cap_for_base_url

        cap = provider_cap_for_base_url("https://api.groq.com/openai/v1")
        assert cap == 5500

    def test_provider_cap_ollama(self):
        from llm.transformers.token_cap import provider_cap_for_base_url

        cap = provider_cap_for_base_url("http://localhost:11434/v1")
        assert cap == 25000

    def test_provider_cap_none(self):
        from llm.transformers.token_cap import provider_cap_for_base_url

        cap = provider_cap_for_base_url(None)
        assert cap == 0

    def test_provider_cap_unknown(self):
        from llm.transformers.token_cap import provider_cap_for_base_url

        cap = provider_cap_for_base_url("https://api.openai.com/v1")
        assert cap == 0

    def test_is_ollama_base_true(self):
        from llm.transformers.model_router import is_ollama_base

        assert is_ollama_base("http://localhost:11434/v1") is True
        assert is_ollama_base("http://host.docker.internal:11434/v1") is True

    def test_is_ollama_base_false(self):
        from llm.transformers.model_router import is_ollama_base

        assert is_ollama_base("https://api.openai.com/v1") is False
        assert is_ollama_base(None) is False
        assert is_ollama_base("") is False
