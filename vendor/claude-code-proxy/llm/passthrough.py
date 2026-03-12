# llm/passthrough.py
"""Direct Anthropic passthrough — bypasses LiteLLM for native Anthropic endpoints.

Ref: https://docs.litellm.ai/docs/pass_through/anthropic_completion
Same concept: send requests in native format, no translation.
Uses httpx to forward requests directly to Anthropic-compatible endpoints.
"""
from __future__ import annotations

import json
import logging
import re
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

_REASONING_TAG_RE = re.compile(r"</?reasoning>")


class PassthroughError(Exception):
    """Raised when passthrough fails and should fall back to litellm pipeline."""
    pass


class PassthroughMetrics:
    """Lightweight metrics collected during passthrough relay."""
    __slots__ = ("tool_use_count", "text_chars", "has_reasoning_leak")

    def __init__(self):
        self.tool_use_count: int = 0
        self.text_chars: int = 0
        self.has_reasoning_leak: bool = False


def _strip_reasoning_from_text_delta(data_str: str) -> str:
    """Strip <reasoning>...</reasoning> from text_delta SSE data.

    Only processes 'content_block_delta' events with text_delta type.
    Returns the modified data string, or empty string if all content was reasoning.
    """
    try:
        data = json.loads(data_str)
    except (json.JSONDecodeError, ValueError):
        return data_str

    if data.get("type") != "content_block_delta":
        return data_str

    delta = data.get("delta", {})
    if delta.get("type") != "text_delta":
        return data_str

    text = delta.get("text", "")
    if "<reasoning>" not in text and "</reasoning>" not in text:
        return data_str

    # Strip reasoning tags and their content
    cleaned = re.sub(r"<reasoning>[\s\S]*?</reasoning>", "", text)
    # Also strip orphan opening/closing tags (split across chunks)
    cleaned = _REASONING_TAG_RE.sub("", cleaned)

    if not cleaned:
        return ""  # Signal to skip this event entirely

    delta["text"] = cleaned
    data["delta"] = delta
    return json.dumps(data, ensure_ascii=False)


class PassthroughClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout, connect=10.0)
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _url(self, path: str = "/v1/messages") -> str:
        return f"{self._base_url}{path}"

    async def create_message(self, body: dict, response_model: str | None = None) -> dict:
        """Non-streaming: POST and return full response.

        response_model: if set, replaces the model field in the response so CC's VSCode
        extension receives the original request model name (e.g. "claude-opus-4-6") rather
        than whatever the upstream provider returned (e.g. "glm-4.7"). Required for Claude
        Code to activate model-gated UI features like the CLAUDE'S PLAN panel.
        """
        body["stream"] = False
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(self._url(), json=body, headers=self._headers)
                resp.raise_for_status()
                result = resp.json()
                # Validate response has actual content (Z.AI can return empty bodies)
                content = result.get("content", [])
                if not content:
                    logger.warning("[passthrough] empty response content from provider")
                    raise PassthroughError("Empty response content from provider")
                # Normalize model name so CC recognizes the response as its own
                if response_model:
                    result["model"] = response_model
                return result
            except httpx.HTTPStatusError as e:
                logger.error("[passthrough] HTTP %d: %s", e.response.status_code, e.response.text[:500])
                raise PassthroughError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e
            except httpx.HTTPError as e:
                logger.error("[passthrough] connection error: %r (cause=%s)", e, e.__cause__)
                raise PassthroughError(repr(e)) from e

    async def stream_message(
        self,
        body: dict,
        strip_reasoning: bool = False,
        response_model: str | None = None,
    ) -> AsyncIterator[str]:
        """Streaming: POST and yield SSE event strings (relay to client).

        Yields complete SSE event strings like 'event: message_start\\ndata: {...}\\n\\n'.
        Collects lightweight metrics (tool_use count, text chars) during relay.
        Optionally strips <reasoning> tags from text_delta events.

        response_model: if set, replaces the model field in the message_start event so the
        VSCode extension receives the original request model name (e.g. "claude-sonnet-4-6")
        rather than whatever the upstream provider returned (e.g. "glm-4.7"). This is required
        for Claude Code to activate model-gated UI features like the CLAUDE'S PLAN panel.
        """
        body["stream"] = True
        self._metrics = PassthroughMetrics()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream(
                    "POST", self._url(), json=body, headers=self._headers,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue

                        # Lightweight metrics parsing (no full JSON decode unless needed)
                        if '"tool_use"' in line:
                            self._metrics.tool_use_count += 1
                        if '"text_delta"' in line:
                            # Rough char count from the line length
                            self._metrics.text_chars += len(line)

                        # Normalize model name in message_start so CC's VSCode extension
                        # receives the original request model (e.g. "claude-sonnet-4-6")
                        # regardless of what the upstream returned.
                        if response_model and '"message_start"' in line and line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                if data.get("type") == "message_start":
                                    data["message"]["model"] = response_model
                                    line = "data: " + json.dumps(data, ensure_ascii=False)
                            except (json.JSONDecodeError, KeyError):
                                pass  # relay as-is if parse fails

                        # Strip reasoning from text_delta events
                        if strip_reasoning and "<reasoning>" in line:
                            self._metrics.has_reasoning_leak = True
                            # Need to parse and modify the SSE data
                            if line.startswith("data: "):
                                data_str = line[6:]
                                cleaned = _strip_reasoning_from_text_delta(data_str)
                                if not cleaned:
                                    continue  # Skip entirely empty delta
                                line = f"data: {cleaned}"

                        yield line + "\n"
            except httpx.HTTPStatusError as e:
                logger.error("[passthrough] stream HTTP %d: %s", e.response.status_code, e.response.text[:500])
                raise PassthroughError(f"HTTP {e.response.status_code}") from e
            except httpx.HTTPError as e:
                logger.error("[passthrough] stream error: %r (cause=%s)", e, e.__cause__)
                raise PassthroughError(repr(e)) from e

    @property
    def metrics(self) -> PassthroughMetrics:
        """Access metrics collected during the last stream_message() call."""
        return getattr(self, "_metrics", PassthroughMetrics())
