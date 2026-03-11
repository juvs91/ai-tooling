"""
Reasoning Handling Transformer

AGNOSTIC transformer for handling reasoning tags and content across ALL models.

Extracts and consolidates reasoning handling logic from streaming.py:
- Reasoning tag detection and stripping (no model-specific)
- Reasoning buffer management (no model-specific)
- Tool call extraction from reasoning content (no model-specific)
- Support for multiple reasoning formats (XML, JSON)

CRITICAL DESIGN REQUIREMENT: AGNOSTIC (NO MODEL-SPECIFIC LOGIC)
- Zero checks of model_name, model patterns, or provider quirks
- Same behavior for ALL models
- Future-proof: New models automatically supported

This is part of the architecture refactoring to eliminate scattered
model-specific logic across multiple files (streaming.py, stream_quality.py, etc.).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from llm.pipeline import Transformer, TransformContext
from llm.transformers.universal_tool_extraction import (
    extract_tool_calls_from_text,
    strip_tool_call_xml,
)

# Pattern for `` tags (used by some models like Qwen, GLM)
_THINK_TAG_RE = re.compile(r'</?think>', re.IGNORECASE)

logger = logging.getLogger(__name__)


class _ReasoningStripper:
    """Stateful stripper for <reasoning>...</reasoning> and `` tags in streaming text.

    Handles tags split across multiple chunks. When STRIP_REASONING=1,
    all content between tags is suppressed.

    AGNOSTIC: Same behavior for ALL models, no model-specific checks.

    Supports both:
    - <reasoning>...</reasoning> tags (used by DeepSeek, R1, etc.)
    - `` tags (used by Qwen, GLM, etc.)
    """

    def __init__(self):
        self._reasoning_inside = False
        self._reasoning_buffer = ""
        self._think_inside = False
        self._think_buffer = ""

    def process(self, text: str) -> str:
        """Process a text chunk, stripping reasoning content.

        Returns text with reasoning tags and their content removed.

        AGNOSTIC: Works for ALL models, no model name checks.
        Handles both <reasoning> and `` tags.
        """
        if not text:
            return text

        # Process `` tags first (simpler, no state machine needed)
        text = self._strip_think_tags(text)

        # Then process <reasoning> tags with state machine
        result = []
        self._reasoning_buffer += text

        while self._reasoning_buffer:
            if not self._reasoning_inside:
                # Look for opening <reasoning> tag
                idx = self._reasoning_buffer.find("<reasoning>")
                if idx == -1:
                    # No opening tag — check for partial tag at end
                    safe_end = len(self._reasoning_buffer)
                    for i in range(1, min(len("<reasoning>"), len(self._reasoning_buffer)) + 1):
                        if "<reasoning>".startswith(self._reasoning_buffer[-i:]):
                            safe_end = len(self._reasoning_buffer) - i
                            break
                    if safe_end > 0:
                        result.append(self._reasoning_buffer[:safe_end])
                        self._reasoning_buffer = self._reasoning_buffer[safe_end:]
                    break
                else:
                    # Emit text before <reasoning> tag
                    if idx > 0:
                        result.append(self._reasoning_buffer[:idx])
                    self._reasoning_buffer = self._reasoning_buffer[idx + len("<reasoning>"):]
                    self._reasoning_inside = True
            else:
                # Inside reasoning — look for closing </reasoning> tag
                idx = self._reasoning_buffer.find("</reasoning>")
                if idx == -1:
                    # No closing tag yet — discard buffered reasoning content
                    self._reasoning_buffer = ""
                    break
                else:
                    # Skip everything up to and including closing tag
                    self._reasoning_buffer = self._reasoning_buffer[idx + len("</reasoning>"):]
                    self._reasoning_inside = False

        return "".join(result)

    def _strip_think_tags(self, text: str) -> str:
        """Strip `` reasoning tags that some models emit (Qwen, GLM).

        AGNOSTIC: Works for ALL models, no model-specific logic.
        """
        if "" in text or "</think>" in text:
            return _THINK_TAG_RE.sub('', text)
        return text


class ReasoningHandlingTransformer(Transformer):
    """
    AGNOSTIC transformer for handling reasoning tags and content.

    Extracts and consolidates reasoning handling logic from streaming.py:
    - Reasoning tag detection and stripping (AGNOSTIC, no model-specific)
    - Reasoning buffer management (AGNOSTIC, no model-specific)
    - Tool call extraction from reasoning content (AGNOSTIC, no model-specific)
    - Support for multiple reasoning formats (XML, JSON)

    AGNOSTIC DESIGN REQUIREMENT:
    - Zero model-specific if/elif blocks (no model_name checks)
    - Zero hardcoded model patterns (no "deepseek-reasoner", "r1", "glm", "minimax" checks)
    - Same behavior for ALL models
    - Future-proof: New models automatically supported

    This replaces scattered model-specific reasoning logic in streaming.py
    with a single AGNOSTIC transformer that works for all models.
    """

    @property
    def name(self) -> str:
        return "reasoning_handling"

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._stripper = _ReasoningStripper()

    async def transform(self, request: object, ctx: TransformContext) -> None:
        """
        Process reasoning content from model response.

        AGNOSTIC: Applies to ALL models, no model-specific logic.

        Extracts tools from reasoning content using tool_prompting functions
        (extract_tool_calls_from_text, _parse_xml_as_tags, strip_tool_call_xml).
        """
        if not self.enabled:
            return

        # Check if this request has tools defined (only process if tools are expected)
        tools_in = len(getattr(request, "tools", []) or [])
        if tools_in == 0:
            # No tools in request - skip reasoning processing
            return

        # Process reasoning content if available
        reasoning_content = getattr(request, "reasoning_content", None)
        if reasoning_content:
            # Strip reasoning tags to get clean reasoning content
            clean_reasoning = self._stripper.process(reasoning_content)

            if clean_reasoning:
                logger.debug(
                    f"[reasoning-handling] Stripped reasoning tags from {len(reasoning_content)} chars -> {len(clean_reasoning)} chars"
                )

                # Extract tool calls from reasoning content (AGNOSTIC)
                tool_calls, remaining_reasoning = extract_tool_calls_from_text(clean_reasoning)

                if tool_calls:
                    logger.debug(
                        f"[reasoning-handling] Extracted {len(tool_calls)} tool calls from reasoning content"
                    )

                # CRITICAL FIX: Clean remaining reasoning to remove orphaned XML tags
                # This prevents XML artifacts from appearing in user-facing reasoning text
                if remaining_reasoning:
                    clean_reasoning = strip_tool_call_xml(remaining_reasoning)
                    if clean_reasoning != remaining_reasoning:
                        logger.info(
                            f"[reasoning-handling] Cleaned orphaned XML tags from reasoning content "
                            f"({len(remaining_reasoning)} -> {len(clean_reasoning)} chars)"
                        )
                    # Update reasoning_content in request with cleaned text
                    request.reasoning_content = clean_reasoning

                    # Add extracted tools to request if not already present
                    # This is AGNOSTIC - works for ALL models, no model-specific checks
                    if not hasattr(request, "tool_calls_from_reasoning") or not request.tool_calls_from_reasoning:
                        # Initialize the attribute
                        request.tool_calls_from_reasoning = []

                    # Append tool calls (don't duplicate existing ones)
                    for tool_call in tool_calls:
                        # Check if this tool call is not already in the list
                        # Use tool_id or name as unique identifier
                        tool_id = tool_call.get("id") or tool_call.get("name", "")
                        if not any(
                            existing.get("id") == tool_id or existing.get("name") == tool_id
                            for existing in (request.tool_calls_from_reasoning or [])
                        ):
                            request.tool_calls_from_reasoning.append(tool_call)
                            logger.debug(
                                f"[reasoning-handling] Added tool from reasoning: {tool_id}"
                            )

                    logger.info(
                        f"[reasoning-handling] Total tool calls from reasoning: {len(request.tool_calls_from_reasoning)}"
                    )

        # Clean up reasoning_content after processing
        # This ensures we don't process it again in downstream transformers
        if reasoning_content:
            setattr(request, "reasoning_content", clean_reasoning if clean_reasoning else None)

        return None
