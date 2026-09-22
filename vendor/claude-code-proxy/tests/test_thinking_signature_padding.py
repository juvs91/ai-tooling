"""Tests for thinking signature padding normalization."""
import base64

import pytest

from llm.transformers.thinking_signature_normalizer import (
    normalize_thinking_signature,
    ThinkingSignatureNormalizer,
)


class TestNormalizeThinkingSignature:
    """normalize_thinking_signature adds base64 padding only when needed."""

    def test_empty_signature_unchanged(self):
        assert normalize_thinking_signature("") == ""

    def test_already_padded_signature_unchanged(self):
        sig = "SGVsbG8gV29ybGQ="  # "Hello World"
        assert normalize_thinking_signature(sig) == sig

    def test_no_padding_adds_two_equals(self):
        sig = "aA"  # missing ==
        normalized = normalize_thinking_signature(sig)
        assert normalized == sig + "=="
        base64.b64decode(normalized, validate=True)

    def test_single_padding_adds_one_equals(self):
        sig = "SGVsbG8="  # already padded with =
        assert normalize_thinking_signature(sig) == sig
        sig2 = "SGVsbG8"  # missing =
        normalized = normalize_thinking_signature(sig2)
        assert normalized == sig2 + "="
        base64.b64decode(normalized, validate=True)

    def test_non_base64_characters_left_unchanged(self):
        sig = "not-base64!@#"
        assert normalize_thinking_signature(sig) == sig

    def test_length_multiple_of_four_unchanged_even_if_invalid(self):
        sig = "SGVsbG8g"  # valid, length 8
        assert normalize_thinking_signature(sig) == sig

    def test_long_signature_gets_padded(self):
        """Simulate a provider that returns a long signature without padding."""
        raw = base64.b64encode(b"x" * 9709).decode("ascii")
        sig = raw.rstrip("=")
        normalized = normalize_thinking_signature(sig)
        assert len(normalized) % 4 == 0
        decoded = base64.b64decode(normalized, validate=True)
        assert decoded == b"x" * 9709


class TestThinkingSignatureNormalizer:
    """ThinkingSignatureNormalizer mutates response content blocks in place."""

    @pytest.mark.asyncio
    async def test_normalizes_thinking_block_signature(self):
        response = {
            "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "aA"},
                {"type": "text", "text": "hello"},
            ]
        }
        normalizer = ThinkingSignatureNormalizer()
        await normalizer.transform(response, None)
        assert response["content"][0]["signature"] == "aA=="
        assert response["content"][1]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_leaves_non_thinking_blocks_unchanged(self):
        response = {
            "content": [
                {"type": "text", "text": "hello"},
            ]
        }
        normalizer = ThinkingSignatureNormalizer()
        await normalizer.transform(response, None)
        assert response["content"][0]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_no_content_unchanged(self):
        response = {"model": "any-model"}
        normalizer = ThinkingSignatureNormalizer()
        await normalizer.transform(response, None)
        assert response["model"] == "any-model"
