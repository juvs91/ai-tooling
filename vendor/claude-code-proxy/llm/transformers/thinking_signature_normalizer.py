"""Thinking signature normalization.

Helpers that ensure thinking-block signatures are valid base64, without
assuming any particular provider or model. The Anthropic Messages API defines
the signature as an opaque value, but clients may validate or re-encode the
field and fail when base64 padding is missing. These utilities add the minimal
required '=' padding only when the string is otherwise base64-safe.
"""
from __future__ import annotations

import re
from typing import Any

from llm.pipeline import Transformer, TransformContext


_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


def normalize_thinking_signature(signature: str) -> str:
    """Return a thinking signature with valid base64 padding.

    The function is idempotent: signatures that are already valid, empty, or
    not composed of base64 alphabet characters are returned unchanged. Only
    signatures whose length is not a multiple of 4 and whose characters all
    belong to the base64 alphabet receive trailing '=' padding.
    """
    if not signature or len(signature) % 4 == 0:
        return signature
    if not _BASE64_RE.match(signature):
        return signature
    pad = (-len(signature)) % 4
    return signature + ("=" * pad)


class ThinkingSignatureNormalizer(Transformer):
    """Response transformer that normalizes base64 padding of thinking signatures.

    Walks response content blocks of type 'thinking' and applies
    `normalize_thinking_signature` to each signature field. Safe for any model
    or provider because it only touches base64-looking strings with incorrect
    padding.
    """

    @property
    def name(self) -> str:
        return "thinking_signature_normalizer"

    async def transform(self, response: Any, ctx: TransformContext) -> None:
        content = getattr(response, "content", None)
        if content is None and isinstance(response, dict):
            content = response.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "thinking":
                    block["signature"] = normalize_thinking_signature(block.get("signature", ""))
