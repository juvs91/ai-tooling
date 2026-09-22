# tests/test_passthrough_sse_normalization.py
"""Tests for SSE normalization in passthrough streaming.

Some Anthropic-compatible providers (e.g. Kimi's /coding/v1 endpoint) emit SSE
lines without the blank-line delimiters required by the SSE spec. Claude Code
and Kimi Code CLI parse events using standard SSE parsers that require ``\\n\\n``
between events. This module verifies that PassthroughClient normalizes such
streams into properly-delimited SSE events.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.passthrough import PassthroughClient


def _event_dict(evt_type: str, **payload: Any) -> dict:
    return {"type": evt_type, **payload}


def _lines_no_blank_delimiters(*events: dict) -> list[str]:
    """Mimic a provider that omits blank lines between events."""
    lines = []
    for evt in events:
        lines.append(f"event:{evt['type']}")
        lines.append(f"data:{json.dumps(evt)}")
    return lines


def _lines_with_blank_delimiters(*events: dict) -> list[str]:
    """Mimic a spec-compliant provider that emits blank lines between events."""
    lines = []
    for evt in events:
        lines.append(f"event:{evt['type']}")
        lines.append(f"data:{json.dumps(evt)}")
        lines.append("")
    return lines


def _mock_response(lines: list[str]) -> MagicMock:
    """Build an httpx streaming response mock that yields *lines*."""
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()

    async def _aiter_lines() -> AsyncIterator[str]:
        for line in lines:
            yield line

    response.aiter_lines = _aiter_lines
    return response


def _mock_client(response: MagicMock):
    """Build an httpx.AsyncClient mock whose stream() context returns *response*."""
    client = AsyncMock()
    # AsyncMock already implements async context manager magic methods;
    # configure their return values directly.
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    cm = AsyncMock()
    cm.__aenter__.return_value = response
    cm.__aexit__.return_value = False
    # stream() is synchronous on httpx.AsyncClient and must return the context
    # manager directly, not a coroutine.
    stream_mock = MagicMock(return_value=cm)
    client.stream = stream_mock
    return client


async def _collect(stream: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
async def test_normalizes_stream_without_blank_event_delimiters():
    """Kimi-style SSE (no blank lines) is rewritten with \\n\\n separators."""
    events = [
        _event_dict("message_start", message={"id": "msg_1", "model": "kimi-for-coding"}),
        _event_dict("content_block_start", index=0, content_block={"type": "text", "text": ""}),
        _event_dict("content_block_delta", index=0, delta={"type": "text_delta", "text": "hi"}),
        _event_dict("content_block_stop", index=0),
        _event_dict("message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 1}),
        _event_dict("message_stop"),
    ]
    response = _mock_response(_lines_no_blank_delimiters(*events))
    client = _mock_client(response)

    pt = PassthroughClient("https://example.com", "sk-test")
    with patch("httpx.AsyncClient", return_value=client):
        chunks = await _collect(pt.stream_message({"model": "kimi-for-coding"}))

    # Every yielded chunk must be a complete event ending with the SSE delimiter.
    for chunk in chunks:
        assert chunk.endswith("\n\n"), f"Chunk missing SSE delimiter: {chunk[:80]!r}"

    # Concatenated chunks must split back into the original number of events.
    full = "".join(chunks)
    parsed = [e for e in full.split("\n\n") if e.strip()]
    assert len(parsed) == len(events)

    # Verify model name and content survive.
    assert '"model": "kimi-for-coding"' in full
    assert '"text": "hi"' in full


@pytest.mark.asyncio
async def test_preserves_spec_compliant_streams():
    """Streams that already contain blank-line delimiters remain valid."""
    events = [
        _event_dict("message_start", message={"id": "msg_2", "model": "kimi-for-coding"}),
        _event_dict("content_block_delta", index=0, delta={"type": "text_delta", "text": "hello"}),
        _event_dict("message_stop"),
    ]
    response = _mock_response(_lines_with_blank_delimiters(*events))
    client = _mock_client(response)

    pt = PassthroughClient("https://example.com", "sk-test")
    with patch("httpx.AsyncClient", return_value=client):
        chunks = await _collect(pt.stream_message({"model": "kimi-for-coding"}))

    full = "".join(chunks)
    # No accidental double delimiters from blank lines in the upstream.
    assert "\n\n\n" not in full
    parsed = [e for e in full.split("\n\n") if e.strip()]
    assert len(parsed) == len(events)


@pytest.mark.asyncio
async def test_normalizes_signature_delta_event():
    """Signature padding normalization still runs inside normalized events."""
    events = [
        _event_dict("message_start", message={"id": "msg_3", "model": "kimi-for-coding"}),
        _event_dict(
            "content_block_delta",
            index=0,
            delta={"type": "signature_delta", "signature": "YWJjZg"},  # missing padding
        ),
        _event_dict("message_stop"),
    ]
    response = _mock_response(_lines_no_blank_delimiters(*events))
    client = _mock_client(response)

    pt = PassthroughClient("https://example.com", "sk-test")
    with patch("httpx.AsyncClient", return_value=client):
        chunks = await _collect(pt.stream_message({"model": "kimi-for-coding"}))

    full = "".join(chunks)
    # Signature should be padded to valid base64.
    assert '"signature": "YWJjZg=="' in full
    # Stream should end with the standard SSE delimiter.
    assert full.endswith("\n\n")


@pytest.mark.asyncio
async def test_response_model_rewriting_with_normalized_sse():
    """The response_model rewrite works on normalized events."""
    events = [
        _event_dict("message_start", message={"id": "msg_4", "model": "upstream-model"}),
        _event_dict("message_stop"),
    ]
    response = _mock_response(_lines_no_blank_delimiters(*events))
    client = _mock_client(response)

    pt = PassthroughClient("https://example.com", "sk-test")
    with patch("httpx.AsyncClient", return_value=client):
        chunks = await _collect(
            pt.stream_message({"model": "kimi-for-coding"}, response_model="kimi-for-coding")
        )

    full = "".join(chunks)
    assert '"model": "kimi-for-coding"' in full
    assert '"model": "upstream-model"' not in full
