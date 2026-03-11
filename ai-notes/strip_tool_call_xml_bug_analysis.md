# CRITICAL BUG ANALYSIS: strip_tool_call_xml Import but Never Used

**Date**: 2026-03-09
**Severity**: 🔴 P0 - CRITICAL
**Status**: 📊 ANALYSIS COMPLETE - FIX READY

## Executive Summary

Two transformers import `strip_tool_call_xml()` function but NEVER use it, causing XML tool call fragments to remain in text content, contaminating user-facing responses and potentially causing extraction failures.

**Files Affected**:
1. `/Users/jeguzman/ai-tooling/vendor/claude-code-proxy/llm/transformers/universal_tool_extraction.py` (line 39)
2. `/Users/jeguzman/ai-tooling/vendor/claude-code-proxy/llm/transformers/reasoning_handling.py` (line 29)

**User Confirmation**: "estoy bien seguro que se tiene que usar, validalo exhaustivamente que no se nos este pasando"
(Translation: "I'm very sure it must be used, validate exhaustively that we're not missing it")

## What is `strip_tool_call_xml()`?

**Location**: `vendor/claude-code-proxy/llm/tool_prompting.py` (lines 1174-1200)

**Purpose**: Last-resort fallback to strip ALL `<tool_call>` XML variants from text.

**Handles**:
- Both `name=` format and GLM `argkv` format
- Complete and incomplete tool calls
- Orphaned opening AND closing inner tags
- Incomplete `<tool_call...>` fragments

**Code**:
```python
def strip_tool_call_xml(text: str) -> str:
    """Strip all