# tests/test_utils.py
"""Tests for utils/utils.py functions."""
import pytest
from unittest.mock import MagicMock
from utils.utils import (
    parse_allowlist,
    filter_tools_allowlist,
    approx_tokens_from_bytes,
    ensure_system_note,
    normalize_tool_choice,
    scale_tokens,
    cached_token_count,
    store_token_count,
    _hash_single_msg,
    _per_msg_cache,
)


class TestParseAllowlist:
    """Tests for parse_allowlist function."""

    def test_empty_string_returns_empty_set(self):
        assert parse_allowlist("") == set()

    def test_none_returns_empty_set(self):
        assert parse_allowlist(None) == set()

    def test_whitespace_only_returns_empty_set(self):
        assert parse_allowlist("   ") == set()

    def test_wildcard_returns_wildcard_set(self):
        assert parse_allowlist("*") == {"*"}

    def test_single_tool_returns_lowercase_set(self):
        assert parse_allowlist("Read") == {"read"}

    def test_multiple_tools_comma_separated(self):
        result = parse_allowlist("Read, Write, Bash")
        assert result == {"read", "write", "bash"}

    def test_handles_extra_whitespace(self):
        result = parse_allowlist("  Read ,  Write  ,  Bash  ")
        assert result == {"read", "write", "bash"}

    def test_ignores_empty_entries(self):
        result = parse_allowlist("Read,,Write,")
        assert result == {"read", "write"}


class TestFilterToolsAllowlist:
    """Tests for filter_tools_allowlist function."""

    @pytest.fixture
    def mock_tools(self):
        """Create mock tools for testing."""
        return [
            MagicMock(name="Read"),
            MagicMock(name="Write"),
            MagicMock(name="Bash"),
            MagicMock(name="Glob"),
        ]

    @pytest.fixture
    def dict_tools(self):
        """Create dict-style tools for testing."""
        return [
            {"name": "Read", "description": "Read files"},
            {"name": "Write", "description": "Write files"},
            {"name": "Bash", "description": "Run bash"},
        ]

    def test_none_tools_returns_none(self):
        kept, dropped = filter_tools_allowlist(None, {"read"})
        assert kept is None
        assert dropped == []

    def test_empty_tools_returns_empty(self):
        kept, dropped = filter_tools_allowlist([], {"read"})
        assert kept == []
        assert dropped == []

    def test_empty_allowlist_returns_tools_unchanged(self):
        tools = [{"name": "Read"}]
        kept, dropped = filter_tools_allowlist(tools, set())
        assert kept == tools
        assert dropped == []

    def test_wildcard_allows_all_tools(self, dict_tools):
        kept, dropped = filter_tools_allowlist(dict_tools, {"*"})
        assert len(kept) == 3
        assert dropped == []

    def test_filters_to_allowed_tools_only(self, dict_tools):
        kept, dropped = filter_tools_allowlist(dict_tools, {"read", "write"})
        assert len(kept) == 2
        names = [t["name"] for t in kept]
        assert "Read" in names
        assert "Write" in names
        assert "Bash" in dropped

    def test_case_insensitive_matching(self, dict_tools):
        # parse_allowlist() always lowercases — pass lowercase to match real usage
        kept, dropped = filter_tools_allowlist(dict_tools, {"read", "write"})
        assert len(kept) == 2


class TestApproxTokensFromBytes:
    """Tests for approx_tokens_from_bytes function."""

    def test_empty_bytes_returns_one(self):
        assert approx_tokens_from_bytes(b"") == 1

    def test_small_content(self):
        # 12 bytes / 6 = 2 tokens
        assert approx_tokens_from_bytes(b"hello world!") == 2

    def test_larger_content(self):
        content = b"x" * 600
        assert approx_tokens_from_bytes(content) == 100


class TestEnsureSystemNote:
    """Tests for ensure_system_note function."""

    def test_adds_note_to_none_system(self):
        request = MagicMock()
        request.system = None
        ensure_system_note(request, "test note")
        assert request.system == "test note"

    def test_prepends_note_to_string_system(self):
        request = MagicMock()
        request.system = "existing system"
        ensure_system_note(request, "new note")
        assert "new note" in request.system
        assert "existing system" in request.system

    def test_dedupes_existing_note(self):
        request = MagicMock()
        request.system = "already has test note here"
        ensure_system_note(request, "test note")
        # Should not modify since note already exists
        assert request.system == "already has test note here"

    def test_adds_to_list_system(self):
        request = MagicMock()
        request.system = [{"type": "text", "text": "existing"}]
        ensure_system_note(request, "new note")
        assert len(request.system) == 2
        assert request.system[0]["text"] == "new note"


class TestNormalizeToolChoice:
    """Tests for normalize_tool_choice function."""

    def test_none_choice_returns_none(self):
        assert normalize_tool_choice(None, []) is None

    def test_empty_kept_tools_returns_none(self):
        choice = {"type": "tool", "name": "Read"}
        assert normalize_tool_choice(choice, []) is None

    def test_auto_type_preserved(self):
        choice = {"type": "auto"}
        tools = [{"name": "Read"}]
        result = normalize_tool_choice(choice, tools)
        assert result == {"type": "auto"}

    def test_any_type_preserved(self):
        choice = {"type": "any"}
        tools = [{"name": "Read"}]
        result = normalize_tool_choice(choice, tools)
        assert result == {"type": "any"}

    def test_tool_type_kept_if_in_allowlist(self):
        choice = {"type": "tool", "name": "Read"}
        tools = [{"name": "Read"}]
        result = normalize_tool_choice(choice, tools)
        assert result == choice

    def test_tool_type_becomes_auto_if_not_in_allowlist(self):
        choice = {"type": "tool", "name": "Write"}
        tools = [{"name": "Read"}]
        result = normalize_tool_choice(choice, tools)
        assert result == {"type": "auto"}


class TestScaleTokens:
    """Tests for scale_tokens function."""

    def test_no_scaling_when_zero_window(self):
        assert scale_tokens(1000, 0) == 1000

    def test_no_scaling_when_window_equals_assumed(self):
        assert scale_tokens(1000, 200_000) == 1000

    def test_no_scaling_when_window_larger_than_assumed(self):
        assert scale_tokens(1000, 300_000) == 1000

    def test_scales_for_128k_window(self):
        # 1000 * (200000 / 131072) = 1525
        assert scale_tokens(1000, 131072) == 1525

    def test_scales_for_32k_window(self):
        # 1000 * (200000 / 32768) = 6103
        assert scale_tokens(1000, 32768) == 6103

    def test_zero_tokens_stays_zero(self):
        assert scale_tokens(0, 131072) == 0

    def test_negative_window_no_scaling(self):
        assert scale_tokens(1000, -1) == 1000


class TestTokenCountCache:
    """Tests for per-message incremental token count cache."""

    def setup_method(self):
        _per_msg_cache.clear()

    def test_cache_miss_returns_none(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert cached_token_count(msgs, "openai/glm-4.7") is None

    def test_store_then_hit(self):
        msgs = [{"role": "user", "content": "hello"}]
        store_token_count(msgs, "openai/glm-4.7", 42)
        assert cached_token_count(msgs, "openai/glm-4.7") == 42

    def test_different_model_is_cache_miss(self):
        msgs = [{"role": "user", "content": "hello"}]
        store_token_count(msgs, "openai/glm-4.7", 42)
        assert cached_token_count(msgs, "openai/deepseek-chat") is None

    def test_different_messages_is_cache_miss(self):
        store_token_count([{"role": "user", "content": "hello"}], "m", 10)
        assert cached_token_count([{"role": "user", "content": "bye"}], "m") is None

    def test_hash_is_deterministic(self):
        msg = {"role": "user", "content": "test"}
        h1 = _hash_single_msg(msg, "model_a")
        h2 = _hash_single_msg(msg, "model_a")
        assert h1 == h2

    def test_eviction_at_max(self):
        from utils.utils import _PER_MSG_MAX
        for i in range(_PER_MSG_MAX + 10):
            store_token_count([{"role": "user", "content": f"msg_{i}"}], "m", i + 1)
        assert len(_per_msg_cache) <= _PER_MSG_MAX

    def test_incremental_hit_on_growing_conversation(self):
        """Per-message cache: adding a new message only misses the new one."""
        msgs_v1 = [{"role": "user", "content": "hello"}]
        store_token_count(msgs_v1, "m", 10)
        assert cached_token_count(msgs_v1, "m") == 10

        # Add a second message — cache miss (new msg uncached)
        msgs_v2 = msgs_v1 + [{"role": "assistant", "content": "world"}]
        assert cached_token_count(msgs_v2, "m") is None

        # Store again with total count — now both are cached
        store_token_count(msgs_v2, "m", 20)
        assert cached_token_count(msgs_v2, "m") is not None

    def test_multi_message_proportional_split(self):
        """Token count is split proportionally by content length."""
        msgs = [
            {"role": "user", "content": "short"},
            {"role": "assistant", "content": "a much longer response here"},
        ]
        store_token_count(msgs, "m", 100)
        total = cached_token_count(msgs, "m")
        # Sum should approximate the stored count (proportional split)
        assert total is not None
        assert 90 <= total <= 110  # allow rounding variance
