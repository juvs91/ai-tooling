# tests/test_server.py
"""Tests for server.py endpoints using FastAPI TestClient."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client with mocked env vars."""
        with patch.dict("os.environ", {
            "PREFERRED_PROVIDER": "openai",
            "SMALL_MODEL": "test-small",
            "BIG_MODEL": "test-big",
            "BUILDING_MODEL": "test-build",
        }):
            from server import app
            return TestClient(app)

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_status_healthy(self, client):
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_returns_provider(self, client):
        response = client.get("/health")
        data = response.json()
        assert "provider" in data

    def test_health_returns_models(self, client):
        response = client.get("/health")
        data = response.json()
        assert "models" in data
        assert "small" in data["models"]
        assert "big" in data["models"]
        assert "building" in data["models"]

    def test_health_returns_timestamp(self, client):
        response = client.get("/health")
        data = response.json()
        assert "timestamp" in data


class TestCountTokensEndpoint:
    """Tests for /v1/messages/count_tokens endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client with mocked dependencies."""
        with patch.dict("os.environ", {
            "PREFERRED_PROVIDER": "openai",
            "SMALL_MODEL": "gpt-4",
            "BIG_MODEL": "gpt-4",
            "BUILDING_MODEL": "gpt-4",
        }):
            from server import app
            return TestClient(app)

    @pytest.fixture
    def basic_request(self):
        """Basic token count request."""
        return {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "Hello, world!"}
            ]
        }

    def test_count_tokens_returns_200(self, client, basic_request):
        with patch("server.token_counter", return_value=10):
            response = client.post("/v1/messages/count_tokens", json=basic_request)
            assert response.status_code == 200

    def test_count_tokens_returns_input_tokens(self, client, basic_request):
        with patch("server.token_counter", return_value=42), \
             patch("server.cached_token_count", return_value=None), \
             patch("server.scale_tokens", side_effect=lambda tokens, _: tokens):
            response = client.post("/v1/messages/count_tokens", json=basic_request)
            data = response.json()
            assert "input_tokens" in data
            assert data["input_tokens"] == 42

    def test_count_tokens_with_system_message(self, client):
        request = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "Hello"}
            ],
            "system": "You are a helpful assistant."
        }
        with patch("server.token_counter", return_value=20):
            response = client.post("/v1/messages/count_tokens", json=request)
            assert response.status_code == 200

    def test_count_tokens_with_list_system(self, client):
        request = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {"role": "user", "content": "Hello"}
            ],
            "system": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"}
            ]
        }
        with patch("server.token_counter", return_value=30):
            response = client.post("/v1/messages/count_tokens", json=request)
            assert response.status_code == 200

    def test_count_tokens_fallback_on_error(self, client, basic_request):
        """Test fallback to heuristic when token_counter fails."""
        with patch("server.token_counter", side_effect=Exception("API error")):
            response = client.post("/v1/messages/count_tokens", json=basic_request)
            # Should still return 200 with fallback calculation
            assert response.status_code == 200
            data = response.json()
            assert "input_tokens" in data
            assert data["input_tokens"] > 0


class TestMessagesEndpoint:
    """Tests for /v1/messages endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        with patch.dict("os.environ", {
            "PREFERRED_PROVIDER": "openai",
            "SMALL_MODEL": "gpt-4",
            "BIG_MODEL": "gpt-4",
            "BUILDING_MODEL": "gpt-4",
            "OPENAI_API_KEY": "test-key",
        }):
            from server import app
            return TestClient(app)

    @pytest.fixture
    def basic_message_request(self):
        """Basic messages request."""
        return {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "Hello"}
            ]
        }

    def test_messages_runs_pipeline(self, client, basic_message_request):
        """Test that the request pipeline is applied to requests."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.id = "test-id"
        mock_response.model = "gpt-4"

        with patch("server._request_pipeline") as mock_pipeline:
            mock_pipeline.process = AsyncMock(return_value=None)
            with patch("proxy.proxy.run_messages", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = (False, mock_response, "primary")
                response = client.post("/v1/messages", json=basic_message_request)
                mock_pipeline.process.assert_called_once()

    def test_messages_returns_413_on_oversize(self, client, basic_message_request):
        """Test that oversized requests return 413."""
        with patch("server._request_pipeline") as mock_pipeline:
            mock_pipeline.process = AsyncMock(side_effect=ValueError("Request too large"))
            response = client.post("/v1/messages", json=basic_message_request)
            assert response.status_code == 413
