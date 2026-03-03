#!/usr/bin/env python3
"""
Test regex patterns for tool calls
"""

import re
import json

# Recreate the regexes from tool_prompting.py
_INNER_TAG = r"(?:input|textarea|arguments|params|json|content|parameters)"
_NAME_ATTR = r"""name=["']([^"']+)["']"""
_REASONING_SKIP = r'(?:\s*)*'

_TOOL_CALL_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*{_REASONING_SKIP}<{_INNER_TAG}>([\s\S]*?)</{_INNER_TAG}>\s*