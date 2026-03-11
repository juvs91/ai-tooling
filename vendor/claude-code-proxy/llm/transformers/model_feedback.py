"""
Model Feedback Transformer

AGNOSTIC transformer for behavior-based feedback generation.

Extracts and consolidates feedback logic from stream_quality.py:
- File extension guidance (AGNOSTIC, based on detected file types)
- Tool restriction guidance (AGNOSTIC, based on reasoning content presence)
- Direct execution guidance (AGNOSTIC, based on response patterns)
- Centralizes behavior patterns instead of model-specific logic

CRITICAL DESIGN REQUIREMENT: AGNOSTIC (NO MODEL-SPECIFIC LOGIC)
- Zero checks of model_name or hardcoded model patterns
- Uses behavior detection (file types, reasoning presence, response patterns)
- Same behavior infrastructure for ALL models
- Future-proof: New models automatically supported

This replaces scattered model-specific if/elif logic in stream_quality.py
with a centralized, AGNOSTIC behavior detection system.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from llm.pipeline import Transformer, TransformContext

logger = logging.getLogger(__name__)


# Pattern detection for behavior-based feedback
# AGNOSTIC: Detect patterns, not model names
_FILE_MENTION_RE = re.compile(r'\.(py|ts|js|go|rs|java|cpp|c)\b')
_REASONING_CONTENT_RE = re.compile(r'<reasoning>', re.IGNORECASE)
_TEXT_ONLY_PATTERN_RE = re.compile(
    r'(?:i will|i am going to|i will|i plan to|i need to|i should|i want to)\s+(?:write|list|read|execute|run)',
    re.IGNORECASE
)
_TOOL_CALL_PATTERN_RE = re.compile(r'<tool_call|tool_use|function_call', re.IGNORECASE)


class ModelFeedbackTransformer(Transformer):
    """
    AGNOSTIC transformer for behavior-based feedback generation.

    Extracts and consolidates feedback logic from stream_quality.py:
    - File extension guidance (based on detected file types in response)
    - Tool restriction guidance (based on reasoning content presence)
    - Direct execution guidance (based on response patterns)

    AGNOSTIC DESIGN REQUIREMENT:
    - Zero model-specific if/elif blocks (no model_name checks)
    - Zero hardcoded model patterns (no "deepseek-reasoner", "r1", "glm", "minimax" checks)
    - Uses behavior detection (file types, reasoning, patterns)
    - Same behavior infrastructure for ALL models
    - Future-proof: New models automatically supported

    This replaces scattered model-specific if/elif logic in stream_quality.py
    with a centralized, AGNOSTIC behavior detection system.
    """

    @property
    def name(self) -> str:
        return "model_feedback"

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    async def transform(self, request: object, ctx: TransformContext) -> None:
        """
        Generate behavior-based feedback using pattern detection.

        AGNOSTIC: Uses behavior detection, not model name checks.

        Returns None (transforms request in-place by modifying messages).

        Note: ctx is not used in this AGNOSTIC implementation - behavior detection
        is based on response patterns, not model characteristics.
        """
        if not self.enabled:
            return

        # Check if this request has tools defined (only apply feedback with tools)
        tools_in = len(getattr(request, "tools", []) or [])
        if tools_in == 0:
            # No tools in request - skip model feedback
            logger.debug(
                f"[model-feedback] Skipping - no tools in request"
            )
            return

        # Get response content for pattern detection
        response_text = self._extract_response_text(request)
        if not response_text:
            logger.debug(
                f"[model-feedback] Skipping - no response text to analyze"
            )
            return

        # AGNOSTIC feedback generation based on detected behavior patterns
        feedback_parts = []

        # 1. File extension guidance (AGNOSTIC: based on detected file types)
        file_feedback = self._detect_file_extension_issues(response_text, request)
        if file_feedback:
            feedback_parts.append(file_feedback)

        # 2. Tool restriction guidance (AGNOSTIC: based on reasoning presence)
        tool_feedback = self._detect_tool_usage_issues(request, response_text)
        if tool_feedback:
            feedback_parts.append(tool_feedback)

        # 3. Direct execution guidance (AGNOSTIC: based on response patterns)
        execution_feedback = self._detect_execution_issues(response_text)
        if execution_feedback:
            feedback_parts.append(execution_feedback)

        if feedback_parts:
            # Combine feedback into single message
            feedback_text = "\n".join(feedback_parts)

            # Add to request messages (append, don't replace)
            if not hasattr(request, "messages") or request.messages is None:
                request.messages = []

            request.messages.append(
                {"role": "assistant", "content": feedback_text[:4000]}
            )

            logger.info(
                f"[model-feedback] Added {len(feedback_parts)} behavior-based feedback parts"
            )
        else:
            logger.debug(
                f"[model-feedback] No behavior issues detected"
            )

        return None

    def _extract_response_text(self, request: object) -> str:
        """Extract text content from request/response for pattern detection.

        AGNOSTIC: Works for ALL models, no model-specific logic.
        """
        text_parts = []

        # Extract from content blocks
        content = getattr(request, "content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)

        # Extract from reasoning content
        reasoning_content = getattr(request, "reasoning_content", "")
        if reasoning_content:
            text_parts.append(reasoning_content)

        # Extract from messages
        messages = getattr(request, "messages", [])
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        text_parts.append(content)
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))

        return "\n".join(text_parts)

    def _detect_file_extension_issues(self, response_text: str, request: object) -> str | None:
        """
        Detect file extension issues using pattern detection.

        AGNOSTIC: Based on detected file types, not model name.
        """
        # Detect file extensions mentioned in response
        mentioned_files = _FILE_MENTION_RE.findall(response_text)

        if not mentioned_files:
            return None

        # Check for inappropriate file extensions for Python projects
        # AGNOSTIC: Uses pattern detection, not model name
        tools = getattr(request, "tools", [])
        has_write_tools = any(
            tool.get("name") in ("Write", "Edit", "NotebookEdit")
            for tool in tools
        )

        # Only apply guidance if project uses Write/Edit tools and mentions .ts/.js
        if has_write_tools:
            ts_js_count = sum(1 for f in mentioned_files if f in ("ts", "js"))
            if ts_js_count > 0:
                py_count = sum(1 for f in mentioned_files if f == "py")
                if py_count == 0 and ts_js_count >= 2:
                    # Multiple TypeScript/JavaScript references, no Python references
                    return (
                        "NOTE: This appears to be a Python project. "
                        "Files typically use .py extension. Consider whether .ts/.js files are appropriate."
                    )

        return None

    def _detect_tool_usage_issues(self, request: object, response_text: str) -> str | None:
        """
        Detect tool usage issues using pattern detection.

        AGNOSTIC: Based on reasoning content and response patterns, not model name.
        """
        # Check if reasoning content is present
        has_reasoning = bool(_REASONING_CONTENT_RE.search(response_text))

        # Check for tool calls in response
        has_tool_calls = bool(_TOOL_CALL_PATTERN_RE.search(response_text))

        # Check for text-only descriptions instead of actual tools
        text_only_patterns = _TEXT_ONLY_PATTERN_RE.findall(response_text)

        # Behavior-based feedback
        if has_reasoning and not has_tool_calls:
            # Reasoning content present but no tool calls → likely restricted model
            return (
                "NOTE: This response contains reasoning content but no tool calls. "
                "Synthesize exclusively from evidence already gathered in this conversation. "
                "Focus on analysis and conclusions rather than executing new tools."
            )

        if not has_tool_calls and text_only_patterns:
            # Text descriptions of future actions instead of actual tools
            return (
                "NOTE: You described actions ('I will write...', 'I need to read...') "
                "but didn't execute any tools. Execute tools directly. "
                "Do not describe what you will do — just do it."
            )

        return None

    def _detect_execution_issues(self, response_text: str) -> str | None:
        """
        Detect execution pattern issues using pattern detection.

        AGNOSTIC: Based on response patterns, not model name.
        """
        # Check for descriptive language about execution
        text_only_patterns = _TEXT_ONLY_PATTERN_RE.findall(response_text)

        # Check for explicit tool calls
        has_tool_calls = bool(_TOOL_CALL_PATTERN_RE.search(response_text))

        if text_only_patterns and not has_tool_calls:
            # Multiple instances of descriptive language without actual tool execution
            return (
                "NOTE: You described planned actions but didn't execute tools. "
                "Execute changes directly with Edit/Write tools. "
                "Do not describe what you will do — just do it."
            )

        return None
