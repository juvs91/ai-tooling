# llm/passthrough.py
"""Direct Anthropic passthrough — bypasses LiteLLM for native Anthropic endpoints.

Ref: https://docs.litellm.ai/docs/pass_through/anthropic_completion
Same concept: send requests in native format, no translation.
Uses httpx to forward requests directly to Anthropic-compatible endpoints.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, AsyncIterator

import httpx

from llm.transformers.thinking_signature_normalizer import normalize_thinking_signature

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
    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0,
                 endpoint_path: str = "/v1/messages"):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._endpoint_path = endpoint_path
        _connect_timeout = float(os.environ.get("PASSTHROUGH_CONNECT_TIMEOUT_S", "30"))
        self._timeout = httpx.Timeout(timeout, connect=_connect_timeout)
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _url(self) -> str:
        return f"{self._base_url}{self._endpoint_path}"

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
                # Normalize thinking signatures to valid base64 padding before
                # relaying the response to the client.
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "thinking":
                        block["signature"] = normalize_thinking_signature(block.get("signature", ""))
                return result
            except httpx.HTTPStatusError as e:
                logger.error("[passthrough] HTTP %d: %s", e.response.status_code, e.response.text[:500])
                raise PassthroughError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e
            except httpx.HTTPError as e:
                logger.error("[passthrough] connection error: %r (cause=%s)", e, e.__cause__)
                raise PassthroughError(repr(e)) from e

    def _transform_sse_event(
        self,
        event_lines: list[str],
        strip_reasoning: bool,
        response_model: str | None,
    ) -> list[str] | None:
        """Apply passthrough transformations to a single SSE event.

        Returns the transformed event lines, or None if the event should be dropped.
        """
        out_lines: list[str] = []
        for line in event_lines:
            # Lightweight metrics parsing (no full JSON decode unless needed)
            if '"tool_use"' in line:
                self._metrics.tool_use_count += 1
            if '"text_delta"' in line:
                self._metrics.text_chars += len(line)

            # Normalize model name in message_start so CC's VSCode extension
            # receives the original request model (e.g. "claude-sonnet-4-6")
            # regardless of what the upstream returned.
            if response_model and '"message_start"' in line and line.startswith("data:"):
                try:
                    data = json.loads(line[5:].lstrip())
                    if data.get("type") == "message_start":
                        data["message"]["model"] = response_model
                        line = "data: " + json.dumps(data, ensure_ascii=False)
                except (json.JSONDecodeError, KeyError):
                    pass  # relay as-is if parse fails

            # Normalize thinking signature padding in signature_delta events
            # before relaying the SSE line to the client.
            if '"signature_delta"' in line and line.startswith("data:"):
                try:
                    data = json.loads(line[5:].lstrip())
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "signature_delta":
                            delta["signature"] = normalize_thinking_signature(delta.get("signature", ""))
                            line = "data: " + json.dumps(data, ensure_ascii=False)
                except (json.JSONDecodeError, KeyError):
                    pass  # relay as-is if parse fails

            # Strip reasoning from text_delta events
            if strip_reasoning and "<reasoning>" in line:
                self._metrics.has_reasoning_leak = True
                if line.startswith("data:"):
                    data_str = line[5:].lstrip()
                    cleaned = _strip_reasoning_from_text_delta(data_str)
                    if not cleaned:
                        return None  # Drop entire event if text delta became empty
                    line = f"data: {cleaned}"

            out_lines.append(line)
        return out_lines

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

        Upstream providers (e.g. Kimi's Anthropic-compatible endpoint) may omit blank
        lines between SSE events. We normalize the stream so every event ends with the
        required \\n\\n delimiter before relaying it to the client.

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
                    current_event: list[str] = []
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.rstrip("\r")
                        if line == "":
                            # Blank line terminates the current event (standard SSE).
                            if current_event:
                                transformed = self._transform_sse_event(
                                    current_event, strip_reasoning, response_model
                                )
                                if transformed:
                                    yield "\n".join(transformed) + "\n\n"
                                current_event = []
                        elif line.startswith("event:") and current_event:
                            # Some providers omit blank lines between events; a new
                            # 'event:' line signals the end of the previous event.
                            transformed = self._transform_sse_event(
                                current_event, strip_reasoning, response_model
                            )
                            if transformed:
                                yield "\n".join(transformed) + "\n\n"
                            current_event = [line]
                        else:
                            current_event.append(line)

                    # Flush any remaining event at stream end.
                    if current_event:
                        transformed = self._transform_sse_event(
                            current_event, strip_reasoning, response_model
                        )
                        if transformed:
                            yield "\n".join(transformed) + "\n\n"
            except httpx.HTTPStatusError as e:
                try:
                    await e.response.aread()
                    error_body = e.response.text[:500]
                except Exception:
                    error_body = "<unreadable>"
                logger.error("[passthrough] stream HTTP %d: %s", e.response.status_code, error_body)
                raise PassthroughError(f"HTTP {e.response.status_code}: {error_body[:200]}") from e
            except httpx.HTTPError as e:
                logger.error("[passthrough] stream error: %r (cause=%s)", e, e.__cause__)
                raise PassthroughError(repr(e)) from e

    @property
    def metrics(self) -> PassthroughMetrics:
        """Access metrics collected during the last stream_message() call."""
        return getattr(self, "_metrics", PassthroughMetrics())


def build_passthrough_body(
    request: Any,
    model: str,
    analysis_phase: str | None = None,
    analysis_thinking: dict | None = None,
    passthrough_thinking: dict | None = None,
) -> dict:
    """Convert MessagesRequest to Anthropic API body dict for passthrough.

    When passthrough_thinking is provided, merges per-model params into the
    body for every phase (model-agnostic). When analysis_phase is one of
    ANALYZING/READ/SYNTHESIZING and analysis_thinking is provided, merges
    those afterwards, keeping its priority.
    """
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": getattr(request, "max_tokens", 4096),
        "messages": [
            m if isinstance(m, dict) else m.dict()
            for m in (request.messages or [])
        ],
    }
    if getattr(request, "system", None):
        system = request.system
        if isinstance(system, list):
            body["system"] = [
                s if isinstance(s, dict) else s.dict() for s in system
            ]
        else:
            body["system"] = system
    if getattr(request, "tools", None):
        body["tools"] = [
            t if isinstance(t, dict) else t.dict() for t in request.tools
        ]
    if getattr(request, "temperature", None) is not None:
        body["temperature"] = request.temperature

    # Merge per-model passthrough thinking params for all phases.
    if passthrough_thinking:
        bare_model = model.split("/")[-1] if "/" in model else model
        model_thinking = passthrough_thinking.get(bare_model)
        if model_thinking:
            body.update(model_thinking)
            logger.info("[passthrough] injected per-model params for %s: %s", bare_model, list(model_thinking.keys()))

    # Inject thinking params for ANALYZING/READ/SYNTHESIZING phases.
    # analysis_thinking comes from ANALYSIS_THINKING_PARAMS env var.
    # SYNTHESIZING needs thinking to verify evidence across multiple hops.
    if analysis_phase in ("ANALYZING", "READ", "SYNTHESIZING") and analysis_thinking:
        # Skip thinking for very large contexts — reasoning overhead causes timeouts
        thinking_cap = int(os.environ.get("THINKING_MAX_INPUT_CHARS", "0"))
        if thinking_cap > 0:
            body_chars = sum(len(str(m)) for m in body.get("messages", []))
            if body_chars > thinking_cap:
                logger.info("[passthrough] SKIP thinking: body_chars=%d > cap=%d", body_chars, thinking_cap)
                return body
        body.update(analysis_thinking)
        logger.info("[passthrough] injected thinking params: %s", list(analysis_thinking.keys()))
    return body


async def try_structural_correction(
    pt: PassthroughClient,
    body: dict,
    malformed_response: object,
    original_model: str,
) -> object | None:
    """One-shot retry: ask the provider to fix its malformed tool_use blocks.

    Appends the malformed response as an assistant turn, then a user correction
    prompt with few-shot native Anthropic format examples. Returns the corrected
    response object, or None if the retry call itself fails.

    Called only when structural issues are detected in the response pipeline and
    ctx.refinement_attempt == 0 (caps at one retry to avoid loops).

    Ref: ADR-0016-native-tool-use-structural-validation.md
    """
    from llm.transformers.structural_tool_validator import build_correction_prompt, pop_malformed_blocks

    malformed_blocks = pop_malformed_blocks()
    if not malformed_blocks:
        return None

    raw_content = getattr(malformed_response, "content", [])
    if isinstance(raw_content, list):
        serialized = [
            b if isinstance(b, dict)
            else (b.dict() if hasattr(b, "dict") else {"type": "text", "text": str(b)})
            for b in raw_content
        ]
    else:
        serialized = [{"type": "text", "text": str(raw_content)}]

    correction_body = {
        **body,
        "messages": list(body.get("messages", [])) + [
            {"role": "assistant", "content": serialized},
            {"role": "user",      "content": build_correction_prompt(malformed_blocks)},
        ],
    }

    try:
        corrected = await pt.create_message(correction_body, response_model=original_model)
        logger.info("[structural-correction] retry succeeded for %d block(s)", len(malformed_blocks))
        return corrected
    except Exception as exc:
        logger.warning("[structural-correction] retry call failed: %s", exc)
        return None
