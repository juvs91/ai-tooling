"""Synchronous unit tests for toksum integration."""
import pytest


def test_toksum_glm_sync():
    """Test GLM-4.7 tokenization via toksum (sync test)."""
    from utils.utils import count_tokens_accurate

    messages = [{"role": "user", "content": "Hello GLM"}]
    tokens = count_tokens_accurate(messages, model="glm-4.7")
    assert tokens > 0
    print(f"✓ GLM-4.7 tokenization works: {tokens} tokens")


def test_toksum_gpt4_sync():
    """Test GPT-4 tokenization via toksum (sync test)."""
    from utils.utils import count_tokens_accurate

    messages = [{"role": "user", "content": "Hello, world!"}]
    tokens = count_tokens_accurate(messages, model="gpt-4")
    assert tokens > 0
    print(f"✓ GPT-4 tokenization works: {tokens} tokens")


def test_toksum_claude_sync():
    """Test Claude tokenization via toksum (sync test)."""
    from utils.utils import count_tokens_accurate

    messages = [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi there!"}
    ]
    tokens = count_tokens_accurate(messages, model="claude-3.5-sonnet-20241022")
    assert tokens > 0
    print(f"✓ Claude tokenization works: {tokens} tokens")


def test_reassemble_trimmed_sync():
    """Test _reassemble_trimmed always returns a list (sync test)."""
    from llm.compressor import _reassemble_trimmed

    system_msg = {"role": "system", "content": "You are a helpful assistant."}
    recent_messages = [
        {"role": "user", "content": "What's the weather?"},
        {"role": "assistant", "content": "I don't have real-time data."},
    ]

    result = _reassemble_trimmed(system_msg, recent_messages)

    assert result is not None, "Should never return None"
    assert isinstance(result, list), "Should return a list"
    assert len(result) > 0, "Should have content"
    print(f"✓ _reassemble_trimmed works correctly: {len(result)} messages")


def test_validate_tool_references_sync():
    """Test _validate_tool_references correctly identifies orphans."""
    from llm.compressor import _validate_tool_references

    valid_messages = [
        {"role": "assistant", "tool_calls": [{"id": "toolu_1", "type": "tool", "name": "weather"}]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "Sunny"},
    ]

    assert _validate_tool_references(valid_messages) is True
    print("✓ Valid tool references detected correctly")

    orphaned_messages = [
        {"role": "assistant", "tool_calls": [{"id": "toolu_1", "type": "tool", "name": "weather"}]},
        {"role": "tool", "tool_call_id": "unknown_tool_2", "content": "Error"},
    ]

    assert _validate_tool_references(orphaned_messages) is False
    print("✓ Orphaned tool references detected correctly")


def test_fix_orphan_tool_messages_sync():
    """Test that _fix_orphan_tool_messages handles orphans."""
    from llm.compressor import _fix_orphan_tool_messages

    messages_with_orphan = [
        {"role": "assistant", "tool_calls": [{"id": "toolu_1", "type": "tool", "name": "weather"}]},
        {"role": "tool", "tool_call_id": "unknown_tool_2", "content": "Error"},
    ]

    result = _fix_orphan_tool_messages(messages_with_orphan)

    orphan_fixed = any(
        msg.get("role") == "user" and "Tool result" in msg.get("content", "")
        for msg in result
    )
    assert orphan_fixed, "Orphaned tool should be converted to user message"
    print(f"✓ Orphan tool message conversion works")


def test_approx_tokens_from_bytes_deprecated_sync():
    """Test that approx_tokens_from_bytes is deprecated but still works."""
    from utils.utils import approx_tokens_from_bytes

    tokens = approx_tokens_from_bytes(b"Hello, world!")
    assert tokens > 0
    print(f"✓ Deprecated function still works: {tokens} tokens")


def test_count_tokens_empty_input_sync():
    """Test that count_tokens_accurate handles empty input correctly."""
    from utils.utils import count_tokens_accurate

    tokens_none = count_tokens_accurate(None, model="gpt-4")
    assert tokens_none == 0
    print(f"✓ None input works: {tokens_none} tokens")

    tokens_empty = count_tokens_accurate([], model="gpt-4")
    assert tokens_empty == 0
    print(f"✓ Empty list works: {tokens_empty} tokens")

    tokens_empty_str = count_tokens_accurate("", model="gpt-4")
    assert tokens_empty_str == 0
    print(f"✓ Empty string works: {tokens_empty_str} tokens")


def test_count_tokens_long_text_sync():
    """Test counting with longer text."""
    from utils.utils import count_tokens_accurate

    long_text = "This is a longer test message to ensure token counting works correctly with more content. " * 10
    messages = [{"role": "user", "content": long_text}]
    tokens = count_tokens_accurate(messages, model="gpt-4")
    assert tokens > 0
    print(f"✓ Long text counting works: {tokens} tokens")


def test_count_tokens_multilingual_sync():
    """Test counting with multilingual text."""
    from utils.utils import count_tokens_accurate

    multilingual_text = "Hello 你好 Bonjour مرحبا"
    messages = [{"role": "user", "content": multilingual_text}]
    tokens = count_tokens_accurate(messages, model="gpt-4")
    assert tokens > 0
    print(f"✓ Multilingual text counting works: {tokens} tokens")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
