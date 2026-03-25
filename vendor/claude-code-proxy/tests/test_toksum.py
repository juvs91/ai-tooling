"""Unit tests for toksum integration and bug fixes."""
import pytest
from unittest.mock import patch


def test_toksum_glm():
    """Test GLM-4.7 tokenization via toksum."""
    from utils.utils import count_tokens_accurate

    messages = [{"role": "user", "content": "Hello GLM"}]
    tokens = count_tokens_accurate(messages, model="glm-4.7")
    assert tokens > 0
    print(f"✓ GLM-4.7 tokenization works: {tokens} tokens")


def test_toksum_gpt4():
    """Test GPT-4 tokenization via toksum."""
    from utils.utils import count_tokens_accurate

    messages = [{"role": "user", "content": "Hello, world!"}]
    tokens = count_tokens_accurate(messages, model="gpt-4")
    assert tokens > 0
    print(f"✓ GPT-4 tokenization works: {tokens} tokens")


def test_toksum_claude():
    """Test Claude tokenization via toksum."""
    from utils.utils import count_tokens_accurate

    messages = [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi there!"}
    ]
    tokens = count_tokens_accurate(messages, model="claude-3.5-sonnet-20241022")
    assert tokens > 0
    print(f"✓ Claude tokenization works: {tokens} tokens")


def test_toksum_fallback_lite_llm():
    """Test fallback to LiteLLM if toksum fails."""
    from utils.utils import count_tokens_accurate

    # toksum is imported locally inside count_tokens_accurate, so patch the source module
    with patch("toksum.count_tokens", side_effect=Exception("toksum failed")):
        messages = [{"role": "user", "content": "Hello"}]
        tokens = count_tokens_accurate(messages, model="gpt-4")
        assert tokens > 0  # Should fall back to LiteLLM
        print(f"✓ LiteLLM fallback works: {tokens} tokens")


def test_string_input():
    """Test string input (e.g., for compression prompts)."""
    from utils.utils import count_tokens_accurate

    tokens = count_tokens_accurate(
        "Hello, this is a compression prompt for testing token counting",
        model="gpt-4"
    )
    assert tokens > 0
    print(f"✓ String input works: {tokens} tokens")


def test_none_input():
    """Test None/empty input."""
    from utils.utils import count_tokens_accurate

    tokens = count_tokens_accurate(None, model="gpt-4")
    assert tokens == 0
    print(f"✓ None input works: {tokens} tokens")

    tokens = count_tokens_accurate([], model="gpt-4")
    assert tokens == 0
    print(f"✓ Empty list works: {tokens} tokens")

    tokens = count_tokens_accurate("", model="gpt-4")
    assert tokens == 0
    print(f"✓ Empty string works: {tokens} tokens")


def test_reassemble_trimmed_always_returns():
    """Test _reassemble_trimmed always returns a list (never None)."""
    from llm.compressor import _reassemble_trimmed

    system_msg = {"role": "system", "content": "You are a helpful assistant."}
    recent_messages = [
        {"role": "user", "content": "What's the weather?"},
    ]

    result = _reassemble_trimmed(system_msg, recent_messages)

    assert result is not None, "Should never return None"
    assert isinstance(result, list), "Should return a list"
    assert len(result) > 0, "Should have content"
    print(f"✓ _reassemble_trimmed works correctly: {len(result)} messages")

    result_no_system = _reassemble_trimmed(None, recent_messages)
    assert result_no_system is not None, "Should never return None with None system"
    assert isinstance(result_no_system, list), "Should return a list with None system"
    print(f"✓ _reassemble_trimmed works with None system msg: {len(result_no_system)} messages")

    result_empty_recent = _reassemble_trimmed(system_msg, [])
    assert result_empty_recent is not None, "Should never return None with empty recent"
    assert isinstance(result_empty_recent, list), "Should return a list with empty recent"
    print(f"✓ _reassemble_trimmed works with empty recent: {len(result_empty_recent)} messages")


def test_reassemble_with_summary_validation():
    """Test _reassemble_with_summary validates tool references."""
    from llm.compressor import _reassemble_with_summary, _validate_tool_references

    system_msg = {"role": "system", "content": "Use tools."}
    summary = "User asked about weather."
    recent_messages = [
        {"role": "user", "content": "What's the weather?"},
    ]

    result = _reassemble_with_summary(system_msg, summary, recent_messages)
    validation = _validate_tool_references(result)
    assert validation is True, "Should be valid without tools"
    print(f"✓ _reassemble_with_summary validation works")

    messages_with_tools = [
        {"role": "user", "content": "Check weather"},
        {"role": "assistant", "tool_calls": [{"id": "toolu_1", "type": "tool", "name": "weather"}]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "Sunny"},
    ]
    result_with_tools = _reassemble_with_summary(
        system_msg, summary, messages_with_tools[2:]
    )
    validation_with_tools = _validate_tool_references(result_with_tools)
    assert validation_with_tools is True
    print(f"✓ _reassemble_with_summary works with tool messages")


def test_count_tokens_multiple_messages():
    """Test counting multiple messages."""
    from utils.utils import count_tokens_accurate

    messages = [
        {"role": "user", "content": "First message"},
        {"role": "assistant", "content": "First response"},
        {"role": "user", "content": "Second message"},
        {"role": "assistant", "content": "Second response"},
    ]
    tokens = count_tokens_accurate(messages, model="gpt-4")
    assert tokens > 0
    print(f"✓ Multiple message counting works: {tokens} tokens")


def test_count_tokens_with_system():
    """Test counting with system message."""
    from utils.utils import count_tokens_accurate

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    tokens = count_tokens_accurate(messages, model="gpt-4")
    assert tokens > 0
    print(f"✓ System message counting works: {tokens} tokens")


def test_count_tokens_allow_approximation_false():
    """Test that allow_approximation=False raises error if toksum fails."""
    from utils.utils import count_tokens_accurate

    # Patch both toksum and LiteLLM so all fallbacks fail
    with patch("toksum.count_tokens", side_effect=Exception("toksum failed")), \
         patch("litellm.token_counter", side_effect=Exception("litellm failed")):
        with pytest.raises(Exception):
            count_tokens_accurate(
                [{"role": "user", "content": "Test"}],
                model="gpt-4",
                allow_approximation=False
            )
    print("✓ allow_approximation=False raises error correctly")


def test_approx_tokens_from_bytes():
    """Test that approx_tokens_from_bytes returns a positive token count."""
    from utils.utils import approx_tokens_from_bytes

    tokens = approx_tokens_from_bytes(b"Hello, world!")
    assert tokens > 0
    print(f"✓ approx_tokens_from_bytes works: {tokens} tokens")


def test_fix_orphan_tool_messages():
    """Test that _fix_orphan_tool_messages handles orphaned tool results."""
    from llm.compressor import _fix_orphan_tool_messages

    messages_with_orphan = [
        {"role": "assistant", "content": "I'll check."},
        {"role": "tool", "tool_call_id": "unknown_tool_1", "content": "No data found"},
    ]

    result = _fix_orphan_tool_messages(messages_with_orphan)
    assert any(msg.get("role") == "user" and "Tool result" in msg.get("content", "") for msg in result)
    print(f"✓ Orphan tool message conversion works")


def test_validate_tool_references():
    """Test _validate_tool_references correctly identifies orphans."""
    from llm.compressor import _validate_tool_references

    valid_messages = [
        {"role": "assistant", "tool_calls": [{"id": "toolu_1", "type": "tool"}]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "Weather is sunny"},
    ]

    assert _validate_tool_references(valid_messages) is True
    print("✓ Valid tool references detected correctly")

    orphaned_messages = [
        {"role": "assistant", "tool_calls": [{"id": "toolu_1", "type": "tool"}]},
        {"role": "tool", "tool_call_id": "unknown_tool_2", "content": "Error"},
    ]

    assert _validate_tool_references(orphaned_messages) is False
    print("✓ Orphaned tool references detected correctly")


def test_count_tokens_long_text():
    """Test counting with longer text."""
    from utils.utils import count_tokens_accurate

    long_text = "This is a longer test message to ensure token counting works correctly with more content. " * 10
    messages = [{"role": "user", "content": long_text}]
    tokens = count_tokens_accurate(messages, model="gpt-4")
    assert tokens > 0
    print(f"✓ Long text counting works: {tokens} tokens")


def test_count_tokens_multilingual():
    """Test counting with multilingual text."""
    from utils.utils import count_tokens_accurate

    multilingual_text = "Hello 你好 Bonjour مرحبا"
    messages = [{"role": "user", "content": multilingual_text}]
    tokens = count_tokens_accurate(messages, model="gpt-4")
    assert tokens > 0
    print(f"✓ Multilingual text counting works: {tokens} tokens")
