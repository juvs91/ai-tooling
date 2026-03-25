"""Simple tests to verify toksum and bug fixes work."""
import pytest


def test_toksum_glm_simple():
    """Test GLM-4.7 tokenization via count_tokens_accurate."""
    from utils.utils import count_tokens_accurate

    messages = [{"role": "user", "content": "Hello GLM"}]
    tokens = count_tokens_accurate(messages, model="glm-4.7")
    assert tokens > 0
    print(f"✓ GLM-4.7 tokenization works: {tokens} tokens")


def test_toksum_gpt4_simple():
    """Test GPT-4 tokenization via count_tokens_accurate."""
    from utils.utils import count_tokens_accurate

    messages = [{"role": "user", "content": "Hello, world!"}]
    tokens = count_tokens_accurate(messages, model="gpt-4")
    assert tokens > 0
    print(f"✓ GPT-4 tokenization works: {tokens} tokens")


def test_reassemble_trimmed_simple():
    """Test _reassemble_trimmed always returns a list."""
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

    # Test with None system msg
    result_no_system = _reassemble_trimmed(None, recent_messages)
    assert result_no_system is not None
    assert isinstance(result_no_system, list)

    print(f"✓ _reassemble_trimmed works correctly: {len(result)} messages")


def test_validate_tool_references_simple():
    """Test _validate_tool_references correctly identifies orphans."""
    from llm.compressor import _validate_tool_references

    # Valid messages (assistant has tool_calls, tool has matching tool_call_id)
    valid_messages = [
        {"role": "assistant", "tool_calls": [{"id": "toolu_1", "type": "tool", "name": "weather"}]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "Sunny"},
    ]

    assert _validate_tool_references(valid_messages) is True

    # Orphaned messages (tool_call_id doesn't match)
    orphaned_messages = [
        {"role": "assistant", "tool_calls": [{"id": "toolu_1", "type": "tool", "name": "weather"}]},
        {"role": "tool", "tool_call_id": "unknown_tool_2", "content": "Error"},
    ]

    assert _validate_tool_references(orphaned_messages) is False


def test_count_tokens_accurate_simple():
    """Test count_tokens_accurate with toksum."""
    from utils.utils import count_tokens_accurate

    messages = [{"role": "user", "content": "Hello GLM"}]
    tokens = count_tokens_accurate(messages, model="glm-4.7")
    assert tokens > 0
    print(f"✓ count_tokens_accurate works: {tokens} tokens")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
