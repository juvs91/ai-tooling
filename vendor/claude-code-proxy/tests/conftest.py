# tests/conftest.py
"""Pytest configuration and shared fixtures."""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def mock_env_vars():
    """Set default environment variables for all tests."""
    env_vars = {
        "OPENAI_API_KEY": "test-openai-key",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "GEMINI_API_KEY": "test-gemini-key",
        "PREFERRED_PROVIDER": "openai",
        "SMALL_MODEL": "gpt-3.5-turbo",
        "BIG_MODEL": "gpt-4",
        "BUILDING_MODEL": "gpt-4",
        "TOOL_ALLOWLIST": "*",
        "POLICY_NOTE_IN_SYSTEM": "1",
        "MAX_INPUT_TOKENS": "0",
        "HARD_BLOCK_OVERSIZE": "0",
    }
    with patch.dict("os.environ", env_vars, clear=False):
        yield


@pytest.fixture
def mock_litellm_completion():
    """Mock litellm.completion for testing."""
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.id = "test-completion-id"
    mock_response.model = "gpt-4"
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Test response"
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 20

    with patch("litellm.completion", return_value=mock_response) as mock:
        yield mock


@pytest.fixture
def mock_litellm_acompletion():
    """Mock litellm.acompletion for testing streaming."""
    from unittest.mock import MagicMock, AsyncMock

    async def mock_generator():
        yield MagicMock(choices=[MagicMock(delta=MagicMock(content="Test "))])
        yield MagicMock(choices=[MagicMock(delta=MagicMock(content="response"))])

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock:
        mock.return_value = mock_generator()
        yield mock


@pytest.fixture
def sample_anthropic_request():
    """Sample Anthropic-style request for testing."""
    return {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "system": "You are a helpful assistant.",
        "stream": False,
    }


@pytest.fixture
def sample_anthropic_request_with_tools():
    """Sample request with tools for testing."""
    return {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2048,
        "messages": [
            {"role": "user", "content": "Read the file test.py"}
        ],
        "system": "You are a coding assistant.",
        "tools": [
            {
                "name": "Read",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"}
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "Write",
                "description": "Write a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["path", "content"]
                }
            }
        ],
        "stream": False,
    }
