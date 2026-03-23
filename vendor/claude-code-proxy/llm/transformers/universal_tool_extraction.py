"""
Universal Tool Extraction Transformer

AGNOSTIC RESPONSE transformer for extracting tools from ALL model outputs.

This is REVOLUTIONARY SOLUTION that resolves multiple root causes from a single
transformer: text generation vs tool execution, quality pipeline blocking, proxy fallback bugs.

Extracts and consolidates universal tool extraction logic from tool_prompting.py:
- Process ALL output types (thinking, content, tools, mixed responses)
- Extract tools from ANY model output format (XML, JSON, native, text)
- Apply to ALL models (no-tools, native tools, mixed) - NO classification dependency
- Universal safenet: always attempt tool extraction from model output
- Future-proof: New models automatically supported without code changes

CRITICAL DESIGN REQUIREMENT: AGNOSTIC (NO MODEL-SPECIFIC LOGIC)
- Zero checks of model_name, model patterns, or provider quirks
- Zero if/elif model_name conditional statements
- Same behavior for ALL models
- Future-proof: New models automatically supported

This is part of the architecture refactoring to eliminate scattered
model-specific logic across multiple files (tool_prompting.py, stream_quality.py, etc.).

TRANSFORMER TYPE: RESPONSE (runs AFTER model returns, NOT before)
INTEGRATION POINT: After Anthropic/LiteLLM endpoint returns response, BEFORE client response
"""
from __future__ import annotations

import json
import logging
import os
import re
from json_repair import repair_json
from types import SimpleNamespace
from typing import Any, Optional, Set, List, Dict

from llm.pipeline import Transformer, TransformContext
# Import ALL helpers from utils modules (no tool_prompting dependency)
from utils.tool_extraction_helpers import (
    _strip_inner_xml_tags,
    _normalize_escaped_xml,
    _parse_xml_as_tags,
    _parse_argkv_tool,
    _type_compatible,
    _get_tool_schema,
    _get_tool_required_fields,
    _get_tool_properties,
    _greedy_extract_json_fields,
    _schema_aware_cleanup,
    _safe_parse_tool_input,
    _repair_tool_input,
)
from utils.tool_extraction_patterns import (
    _TOOL_CALL_OPEN,
    _TOOL_CALL_RE,
    _TOOL_CALL_FALLBACK_RE,
    _TOOL_CALL_GREEDY_RE,
    _TOOL_CALL_BARE_RE,
    _TOOL_CALL_ARGKV_RE,
    _TOOL_CALL_ARGKV_LOOSE_RE,
    _TOOL_DILUTED_RE,
    _ARG_KV_PAIR_RE,
    _XML_PARAM_TAG_RE,
    _XML_ATTR_PARAM_RE,
    _CDATA_RE,
    _REAL_NAME_RE,
    _TOOL_CALL_CLOSE,
    _REASONING_SKIP,
    _INNER_TAG,
    _NAME_ATTR,
    _PARTIAL_TOOL_RE,
    _PARTIAL_ARGKV_RE,
    _PARTIAL_XML_TAGS_RE,
    _DSML_INVOKE_RE,
    _DSML_PARAM_RE,
    _DSML_INVOKE_OPEN,
    _DSML_FCALLS_OPEN,
    _DSML_INVOKE_CLOSE,
    _DSML_FCALLS_CLOSE,
)
from utils.tool_utils import (
    build_valid_tool_names as _build_valid_tool_names,
    validate_tool_name,
    normalize_tool_name as _normalize_tool_name,
)
from utils.utils import get_tool_name, make_tool_id, to_dict
from utils.metrics import metrics

logger = logging.getLogger(__name__)


def _ensure_request_object(request: Any) -> Any:
    """Convert dict-based responses to object-based for attribute access.

    Passthrough responses from pt.create_message() are dicts; Pydantic MessagesResponse
    objects forbid arbitrary attribute assignment. Both are converted to SimpleNamespace
    so the transformer can freely set transient state (xml_tool_buffer, etc.).

    NOTE: The caller must use ctx.extracted_tool_calls (not request.*) for state that
    needs to survive after this method returns a copy.
    """
    if isinstance(request, dict):
        return SimpleNamespace(**request)
    try:
        from pydantic import BaseModel
        if isinstance(request, BaseModel):
            return SimpleNamespace(**request.model_dump())
    except ImportError:
        pass
    return request


def _get_block_type(block: Any) -> str:
    """Return the 'type' field from a content block (dict or Pydantic object)."""
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _get_block_text(block: Any) -> str:
    """Return the 'text' field from a content block (dict or Pydantic object)."""
    if isinstance(block, dict):
        return block.get("text", "")
    return getattr(block, "text", "")


# ---------------------------------------------------------------------------
# Core extraction functions
# ---------------------------------------------------------------------------

def _extract_dsml_tool_calls(text: str, tools: list | None = None) -> list[dict]:
    """Extract tool calls from DeepSeek-R1 native <｜DSML｜invoke> format.

    DeepSeek-R1 (deepseek-reasoner) ignores injected <tool_call> XML instructions
    and outputs its own DSML token format instead. This function parses that format
    and converts it to standard Anthropic tool_use blocks.

    Format example:
        <｜DSML｜function_calls>
          <｜DSML｜invoke name="Glob">
            <｜DSML｜parameter name="pattern" string="true">**/*.md</｜DSML｜parameter>
          </｜DSML｜invoke>
        </｜DSML｜function_calls>
    """
    tool_blocks: list[dict] = []
    for m in _DSML_INVOKE_RE.finditer(text):
        name = _normalize_tool_name(m.group(1).strip())
        body = m.group(2)
        params: dict = {}
        for p in _DSML_PARAM_RE.finditer(body):
            val = p.group(2).strip()
            try:
                params[p.group(1)] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                params[p.group(1)] = val
        parsed_input = params if params else _safe_parse_tool_input("", name, tools=tools)
        tool_blocks.append({
            "type": "tool_use",
            "id": make_tool_id(),
            "name": name,
            "input": parsed_input,
        })
    return tool_blocks


def extract_tool_calls_from_text(
    text: str,
    valid_tool_names: set[str] | None = None,
    tools: list | None = None,
) -> tuple[list[dict], str]:
    """
    Extract XML tool calls from text response.

    Returns:
        (tool_call_blocks, remaining_text)
        - tool_call_blocks: list of Anthropic tool_use dicts
        - remaining_text: text with tool_call XML removed

    Resilience guarantees:
        - Malformed JSON -> repaired or wrapped as {"raw_input": ...}
        - Invalid XML structure -> ignored (stays as text)
        - Empty input -> empty dict {}
        - Tolerates model using wrong inner tags (textarea, arguments, etc.)
        - Never raises exceptions
    """
    if not text:
        return [], ""

    # Fast path: DeepSeek-R1 native DSML format — check first (unambiguous marker)
    # Use \uff5c (fullwidth pipe) as guard — avoids false positives on text containing "DSML" literally
    if "\uff5cDSML\uff5c" in text:
        dsml_blocks = _extract_dsml_tool_calls(text, tools=tools)
        if dsml_blocks:
            # Strip all DSML content from remaining text
            remaining = _DSML_INVOKE_RE.sub("", text)
            # Also strip outer function_calls wrapper if present
            remaining = re.sub(
                r'<[|\uff5c]DSML[|\uff5c]function_calls>[\s\S]*?</[|\uff5c]DSML[|\uff5c]function_calls>',
                "", remaining, flags=re.DOTALL,
            ).strip()
            print(f"[no-tools] DSML: extracted {len(dsml_blocks)} tool call(s) from DeepSeek-R1 format")
            return dsml_blocks, remaining

    tool_blocks: list[dict] = []
    used_re = _TOOL_CALL_RE
    try:
        for match in _TOOL_CALL_RE.finditer(text):
            name = _normalize_tool_name(match.group(1).strip())
            raw_input = match.group(2)
            parsed_input = _safe_parse_tool_input(raw_input, name, tools=tools)
            tool_blocks.append({
                "type": "tool_use",
                "id": make_tool_id(),
                "name": name,
                "input": parsed_input,
            })

        # Fallback: try permissive regex if primary found nothing
        if not tool_blocks and "<tool_call" in text:
            for match in _TOOL_CALL_FALLBACK_RE.finditer(text):
                name = _normalize_tool_name(match.group(1).strip())
                inner_tag = match.group(2)
                raw_input = match.group(3)
                print(f"[no-tools] WARNING: Model used <{inner_tag}> instead of <input> for tool '{name}' — parsed via fallback regex")
                parsed_input = _safe_parse_tool_input(raw_input, name, tools=tools)
                tool_blocks.append({
                    "type": "tool_use",
                    "id": make_tool_id(),
                    "name": name,
                    "input": parsed_input,
                })
            used_re = _TOOL_CALL_FALLBACK_RE

        # 3rd fallback: bare regex (no inner tags at all)
        # Also handles tool calls with NO input (e.g. EnterPlanMode, ExitPlanMode)
        if not tool_blocks and "<tool_call" in text:
            for match in _TOOL_CALL_BARE_RE.finditer(text):
                name = _normalize_tool_name(match.group(1).strip())
                raw_content = match.group(2).strip()
                print(f"[no-tools] BARE regex match for tool '{name}' (no inner tags, content={len(raw_content)} chars)")
                parsed_input = _safe_parse_tool_input(raw_content, name, tools=tools)
                tool_blocks.append({
                    "type": "tool_use",
                    "id": make_tool_id(),
                    "name": name,
                    "input": parsed_input,
                })
            used_re = _TOOL_CALL_BARE_RE

        # 4th fallback: GLM arg_key/arg_value format
        if not tool_blocks and "<tool_call>" in text:
            for match in _TOOL_CALL_ARGKV_RE.finditer(text):
                parsed = _parse_argkv_tool(match)
                name = _normalize_tool_name(parsed["name"])
                print(f"[no-tools] ARGKV regex match for tool '{name}' keys={list(parsed['input'].keys())}")
                tool_blocks.append({
                    "type": "tool_use",
                    "id": make_tool_id(),
                    "name": name,
                    "input": parsed["input"],
                })
            if tool_blocks:
                used_re = _TOOL_CALL_ARGKV_RE

        # 5th fallback: loose argkv — argkv format with missing/truncated </tool_call>.
        # Handles non-streaming responses that were cut off before the closing tag.
        # Only matches pairs where </arg_value> is present and complete.
        if not tool_blocks and "<tool_call>" in text:
            for match in _TOOL_CALL_ARGKV_LOOSE_RE.finditer(text):
                parsed = _parse_argkv_tool(match)
                name = _normalize_tool_name(parsed["name"])
                print(f"[no-tools] ARGKV-LOOSE match for tool '{name}' keys={list(parsed['input'].keys())} (no closing tag)")
                tool_blocks.append({
                    "type": "tool_use",
                    "id": make_tool_id(),
                    "name": name,
                    "input": parsed["input"],
                })
            if tool_blocks:
                used_re = _TOOL_CALL_ARGKV_LOOSE_RE

        # 6th fallback: diluted XML format (models invent <tool_name>/<args> after prompt dilution)
        if not tool_blocks and "<tool_name>" in text:
            for match in _TOOL_DILUTED_RE.finditer(text):
                name = _normalize_tool_name(match.group(1).strip())
                raw_input = match.group(2)
                print(f"[no-tools] DILUTED regex match for tool '{name}' (model used <tool_name>/<args> format)")
                parsed_input = _safe_parse_tool_input(raw_input, name, tools=tools)
                tool_blocks.append({
                    "type": "tool_use",
                    "id": make_tool_id(),
                    "name": name,
                    "input": parsed_input,
                })
            if tool_blocks:
                used_re = _TOOL_DILUTED_RE

    except Exception as e:
        print(f"[no-tools] Error extracting tool calls: {e}")
        return [], text

    # Filter out hallucinated tool names
    if valid_tool_names and tool_blocks:
        original_count = len(tool_blocks)
        tool_blocks = [tc for tc in tool_blocks if validate_tool_name(tc.get("name", ""), valid_tool_names)]
        filtered = original_count - len(tool_blocks)
        if filtered:
            print(f"[no-tools] Filtered {filtered} tool call(s) with invalid names (valid: {', '.join(sorted(valid_tool_names))})")

    if not tool_blocks:
        if "<tool_call" in text:
            print(f"[no-tools] WARNING: Found <tool_call> in text but ALL regexes failed. First 500 chars: {text[:500]}")
        return [], text

    remaining = used_re.sub("", text).strip()
    return tool_blocks, remaining


def strip_tool_call_xml(text: str) -> str:
    """Strip all <tool_call> XML variants from text. Last-resort fallback.

    Handles both name= format and GLM argkv format, complete and incomplete.
    """
    if not text:
        return text
    has_tool_call = "<tool_call" in text
    has_inner_tags = "<arg_key>" in text or "<arg_value>" in text or "</arg_key>" in text or "</arg_value>" in text
    if not has_tool_call and not has_inner_tags:
        return text
    # Remove complete tool calls (all 4 formats)
    cleaned = _TOOL_CALL_RE.sub("", text)
    cleaned = _TOOL_CALL_FALLBACK_RE.sub("", cleaned)
    cleaned = _TOOL_CALL_BARE_RE.sub("", cleaned)
    cleaned = _TOOL_CALL_ARGKV_RE.sub("", cleaned)
    # Remove argkv tool calls with missing/truncated </tool_call> closing tag.
    # _TOOL_CALL_ARGKV_LOOSE_RE uses (?:</tool_call>|$) so it only strips when the
    # arg pairs are complete — prevents false positives on partial content.
    cleaned = _TOOL_CALL_ARGKV_LOOSE_RE.sub("", cleaned)
    # Remove incomplete <tool_call...> fragments (no closing tag)
    # Bounded match (8000/2000 chars) prevents destroying all content after a false positive
    cleaned = re.sub(r'<tool_call\s+[^>]*>(?:(?!</tool_call>)[\s\S]){0,8000}$', '', cleaned)
    cleaned = re.sub(r'<tool_call>[A-Za-z]\w*(?:<arg_key>[\s\S]*?</arg_key>\s*<arg_value>[\s\S]*?</arg_value>)*(?:(?!</tool_call>)[\s\S]){0,2000}$', '', cleaned)
    # Orphaned opening AND closing inner tags
    cleaned = re.sub(r'</?(?:tool_call|input|textarea|arguments|params|arg_key|arg_value)>', '', cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Recovery functions
# ---------------------------------------------------------------------------

def recover_truncated_deterministic(
    partial_xml: str,
    tools: list | None = None,
) -> list[dict] | None:
    """
    Attempt to recover a truncated <tool_call> XML deterministically (no LLM).

    Strategy:
      1. Extract tool name from partial XML
      2. Extract whatever JSON is there (even truncated)
      3. Use json_repair to close brackets/braces
      4. Validate that required fields are present
      5. Return tool_use dict if valid, None otherwise

    This is FAST, FREE, and DETERMINISTIC — no API call needed.
    """
    if not partial_xml:
        return None

    match = _PARTIAL_TOOL_RE.search(partial_xml)
    if not match:
        # Try argkv format: <tool_call>Name<arg_key>...
        match_argkv = _PARTIAL_ARGKV_RE.search(partial_xml)
        if match_argkv:
            tool_name = match_argkv.group(1).strip()
            args_portion = match_argkv.group(2)
            # Extract all complete key-value pairs
            input_dict = {}
            for kv in _ARG_KV_PAIR_RE.finditer(args_portion):
                input_dict[kv.group(1).strip()] = kv.group(2)
            if input_dict:
                required = _get_tool_required_fields(tool_name, tools)
                missing = required - set(input_dict.keys())
                if not missing:
                    print(f"[no-tools] Deterministic recovery OK (argkv) for '{tool_name}': keys={list(input_dict.keys())}")
                    return [{"type": "tool_use", "id": make_tool_id(), "name": tool_name, "input": input_dict}]
                print(f"[no-tools] Deterministic recovery (argkv) for '{tool_name}' missing: {missing}")

        # Try XML-as-tags format: <tool_call name="Write"><file_path>...</file_path><content>...</content>
        # Also handles attributed format: <parameter name="file_path">...</parameter>
        match_xml_tags = _PARTIAL_XML_TAGS_RE.search(partial_xml)
        if match_xml_tags:
            tool_name = match_xml_tags.group(1).strip()
            tags_content = match_xml_tags.group(2)
            remaining = partial_xml[match_xml_tags.end():]
            all_content = tags_content + remaining
            input_dict = {}
            # Try simple XML-as-tags first
            for tag_match in _XML_PARAM_TAG_RE.finditer(all_content):
                input_dict[tag_match.group(1)] = tag_match.group(2)
            # Try attributed format as fallback
            if not input_dict:
                for attr_match in _XML_ATTR_PARAM_RE.finditer(all_content):
                    input_dict[attr_match.group(1)] = attr_match.group(2)
            # Also try to capture a truncated last param (unclosed tag)
            if remaining.strip():
                open_tag = re.search(r'<(\w+)>([\s\S]*)$', remaining)
                if open_tag and open_tag.group(1) not in input_dict:
                    input_dict[open_tag.group(1)] = open_tag.group(2)
            if input_dict:
                required = _get_tool_required_fields(tool_name, tools)
                missing = required - set(input_dict.keys())
                if not missing:
                    print(f"[no-tools] Deterministic recovery OK (xml-as-tags) for '{tool_name}': keys={list(input_dict.keys())}")
                    return [{"type": "tool_use", "id": make_tool_id(), "name": tool_name, "input": input_dict}]
                print(f"[no-tools] Deterministic recovery (xml-as-tags) for '{tool_name}' missing: {missing}")

        return None

    tool_name = match.group(1).strip()
    raw_json = match.group(2).strip()

    if not raw_json:
        return None

    # Strip any trailing XML tags that got caught
    for tag in ["</input>", "</tool_call>", "</textarea>", "</arguments>", "</params>"]:
        raw_json = raw_json.split(tag)[0]

    # Try json_repair on the truncated JSON
    parsed = None
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        try:
            repaired = repair_json(raw_json, return_objects=True)
            if isinstance(repaired, dict):
                parsed = repaired
                print(f"[no-tools] Deterministic repair succeeded for '{tool_name}'")
        except Exception:
            pass

    if not isinstance(parsed, dict):
        print(f"[no-tools] Deterministic repair failed for '{tool_name}' ({len(raw_json)} chars of JSON)")
        return None

    # Validate: check that required fields are present
    required = _get_tool_required_fields(tool_name, tools)
    missing = required - set(parsed.keys())
    if missing:
        print(f"[no-tools] Deterministic repair for '{tool_name}' missing required fields: {missing}")
        return None

    # Check for obviously truncated string values (value ends with incomplete content)
    # This catches Write/Edit where "content" got cut mid-file
    for key, value in parsed.items():
        if isinstance(value, str) and len(value) > 200:
            raw_value_start = f'"{key}"'
            if raw_value_start in raw_json:
                key_pos = raw_json.index(raw_value_start)
                after_key = raw_json[key_pos:]
                if value not in after_key and len(value) > 500:
                    print(f"[no-tools] Deterministic repair for '{tool_name}': field '{key}' appears truncated ({len(value)} chars), rejecting")
                    return None

    print(f"[no-tools] Deterministic recovery OK for '{tool_name}': keys={list(parsed.keys())}")
    return [{
        "type": "tool_use",
        "id": make_tool_id(),
        "name": tool_name,
        "input": parsed,
    }]


async def recover_incomplete_tool_call(
    partial_xml: str,
    tools: list | None,
    model: str,
    api_key: str,
    api_base: str | None = None,
    timeout_s: float = 3.0,
) -> list[dict] | None:
    """
    Attempt to reconstruct truncated <tool_call> XML.

    Strategy (ordered by reliability):
      1. Deterministic: json_repair + schema validation (instant, free)
      2. LLM retry: ask classifier model to complete the XML (slow, paid)

    Returns list of tool_use dicts on success, None on failure.
    """
    if not partial_xml:
        return None

    # Allow disabling recovery via env var
    if os.environ.get("DISABLE_TOOL_RECOVERY", "").strip() == "1":
        return None

    # --- Step 1: Deterministic recovery (no LLM) ---
    deterministic = recover_truncated_deterministic(partial_xml, tools)
    if deterministic:
        return deterministic

    # --- Step 2: LLM recovery (fallback) ---
    if not api_key:
        return None

    # Extract tool name from partial XML if possible
    name_match = re.search(r'<tool_call\s+' + _NAME_ATTR, partial_xml)
    tool_name = name_match.group(1) if name_match else None

    # Find tool definition for context
    tool_def = ""
    if tools and tool_name:
        for t in tools:
            if get_tool_name(t) == tool_name:
                tool_def = json.dumps(to_dict(t), ensure_ascii=False)[:500]
                break

    # Extract text context before <tool_call for better model understanding
    context_text = ""
    tc_idx = partial_xml.find("<tool_call")
    if tc_idx > 0:
        context_text = partial_xml[:tc_idx].strip()[-500:]  # last 500 chars of context

    prompt = (
        "Complete this truncated XML tool call. "
        "Respond ONLY with the complete <tool_call> XML, nothing else.\n\n"
    )
    if context_text:
        prompt += f"Context (what the assistant was doing):\n{context_text}\n\n"
    xml_start = tc_idx if tc_idx >= 0 else 0
    prompt += f"Partial XML:\n{partial_xml[xml_start:xml_start + 2000]}\n\n"
    if tool_def:
        prompt += f"Tool schema:\n{tool_def}\n"

    max_recovery_tokens = int(os.environ.get("RECOVERY_MAX_TOKENS", "2048"))

    try:
        import asyncio
        import litellm
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_recovery_tokens,
                temperature=0,
                api_key=api_key,
                api_base=api_base,
            ),
            timeout=timeout_s,
        )
        content = response.choices[0].message.content or ""
        _recovery_valid_names = _build_valid_tool_names(tools)
        tool_blocks, _ = extract_tool_calls_from_text(
            content, valid_tool_names=_recovery_valid_names, tools=tools
        )
        if tool_blocks:
            print(f"[no-tools] Recovered {len(tool_blocks)} tool call(s) via LLM retry")
            return tool_blocks
        print("[no-tools] LLM recovery response had no valid tool calls")
    except asyncio.TimeoutError:
        print(f"[no-tools] LLM tool call recovery timed out ({timeout_s}s)")
    except Exception as e:
        print(f"[no-tools] LLM tool call recovery failed: {type(e).__name__}: {e}")

    return None


# ---------------------------------------------------------------------------
# Streaming XML buffer (state machine)
# ---------------------------------------------------------------------------

class XmlToolBuffer:
    """
    State machine for detecting <tool_call> XML tags in a streaming text.
    Feed text chunks, get back ordered segments of text and tool_calls.
    """

    def __init__(self, valid_tool_names: set[str] | None = None, tools: list | None = None):
        self.buffer: str = ""
        self.in_tool: bool = False
        self.valid_tool_names: set[str] = valid_tool_names or set()
        self.tools: list | None = tools
        self._chunk_count: int = 0

    def feed(self, text: str) -> list[dict]:
        """
        Feed a new text chunk.

        Returns list of segments in order:
            [{"type": "text", "text": "..."}, {"type": "tool_call", "name": ..., "input": {...}}, ...]
        """
        self.buffer += text
        self._chunk_count += 1
        # Throttled logging: only every 50th chunk to avoid log noise
        # (a 5K tool call generates ~1500 chunks of 1-10 chars each)
        if self._chunk_count % 50 == 0 and self.in_tool:
            print(f"[xml-buffer] accumulating: {len(self.buffer)}c in_tool={self.in_tool}", flush=True)
        return self._drain()

    def _has_plausible_tool_call(self) -> bool:
        """Check if buffer contains a plausible tool call, not just documentation mentioning <tool_call>."""
        buf = self.buffer
        # Fast check: DeepSeek-R1 DSML format (｜DSML｜invoke is unambiguous)
        if _DSML_INVOKE_OPEN in buf or _DSML_FCALLS_OPEN in buf:
            return True
        idx = buf.find("<tool_call")
        if idx == -1:
            return False
        after = buf[idx + len("<tool_call"):]
        if not after:
            return True  # Ambiguous at buffer end — treat as possible tool (conservative)
        first = after[0]
        # Standard format: <tool_call name="...   or   <tool_call\n
        if first in (' ', '\t', '\n', '\r'):
            return True
        # GLM format: <tool_call>ToolName...
        # Also handles <tool_call>\nToolName\n<arg_key>... (whitespace before name)
        if first == '>':
            rest = after[1:]
            # Skip optional whitespace before tool name
            stripped = rest.lstrip(' \t\n\r')
            if stripped and stripped[0].isalpha():
                return True
            return False  # <tool_call>` or completely non-alpha after > — not a tool
        return False  # <tool_call(, <tool_call? — not a tool

    def flush(self) -> list[dict]:
        """Flush remaining buffer at stream end.

        Returns: "text", "tool_call", or "incomplete_tool_call" segments.

        Recovery order:
          0. DeepSeek-R1 DSML format (｜DSML｜invoke) — checked before standard path.
          1. Try _TOOL_CALL_ARGKV_LOOSE_RE — extracts argkv tool even without </tool_call>.
             Only activates when ALL present arg pairs have complete </arg_key>/</arg_value> tags.
          2. Warn + return incomplete_tool_call (existing behaviour).
        """
        if not self.buffer:
            return []

        # Recovery path 0: DeepSeek-R1 DSML format
        if _DSML_INVOKE_OPEN in self.buffer or _DSML_FCALLS_OPEN in self.buffer:
            dsml_blocks = _extract_dsml_tool_calls(self.buffer, tools=self.tools if hasattr(self, 'tools') else None)
            if dsml_blocks:
                segments: list[dict] = []
                # Emit any text before the first DSML marker as text
                first_m = next(_DSML_INVOKE_RE.finditer(self.buffer), None)
                fc_idx = self.buffer.find(_DSML_FCALLS_OPEN)
                start_idx = min(
                    first_m.start() if first_m else len(self.buffer),
                    fc_idx if fc_idx >= 0 else len(self.buffer),
                )
                if start_idx > 0:
                    prefix = self.buffer[:start_idx].strip()
                    if prefix:
                        segments.append({"type": "text", "text": prefix})
                print(f"[xml-buffer] flush: extracted {len(dsml_blocks)} DSML tool call(s) (DeepSeek-R1 format)")
                self.buffer = ""
                self.in_tool = False
                for b in dsml_blocks:
                    segments.append({"type": "tool_call", "name": b["name"], "input": b["input"]})
                return segments

        if "<tool_call" in self.buffer and self._has_plausible_tool_call():
            segments: list[dict] = []
            idx = self.buffer.find("<tool_call")
            if idx > 0:
                prefix = self.buffer[:idx].strip()
                if prefix:
                    segments.append({"type": "text", "text": prefix})
            tool_part = self.buffer[idx:] if idx >= 0 else self.buffer

            # --- Recovery: loose argkv (handles truncated stream without </tool_call>) ---
            loose_match = _TOOL_CALL_ARGKV_LOOSE_RE.search(tool_part)
            if loose_match:
                parsed = _parse_argkv_tool(loose_match)
                name = parsed["name"]
                valid = (not self.valid_tool_names) or validate_tool_name(name, self.valid_tool_names)
                if valid:
                    print(
                        f"[xml-buffer] flush: recovered truncated argkv tool '{name}' "
                        f"keys={list(parsed['input'].keys())} (no </tool_call>)",
                        flush=True,
                    )
                    self.buffer = ""
                    self.in_tool = False
                    segments.append({"type": "tool_call", "name": name, "input": parsed["input"]})
                    return segments
                print(f"[xml-buffer] flush: loose argkv hallucinated tool '{name}' — emitting as text")
            # ------------------------------------------------------------------

            print(f"[no-tools] WARNING: flushing incomplete tool call "
                  f"({len(tool_part)} chars). First 300: {tool_part[:300]}")
            segments.append({"type": "incomplete_tool_call", "text": tool_part})
            self.buffer = ""
            self.in_tool = False
            return segments
        result = [{"type": "text", "text": self.buffer}]
        self.buffer = ""
        self.in_tool = False
        return result

    # -- internal --

    def _drain(self) -> list[dict]:
        """Process buffer and extract all complete segments."""
        segments: list[dict] = []
        while self.buffer:
            if not self.in_tool:
                segment = self._try_extract_text()
            else:
                segment = self._try_extract_tool()
            if segment is None:
                break
            segments.append(segment)
        return segments

    def _try_extract_text(self) -> dict | None:
        """Try to extract text before a <tool_call> or DSML tag, or return None if need more data."""
        search_start = 0
        while True:
            idx = self.buffer.find(_TOOL_CALL_OPEN, search_start)

            # Also check for DSML format (DeepSeek-R1 native tool calls)
            dsml_invoke_idx = self.buffer.find(_DSML_INVOKE_OPEN, search_start)
            dsml_fcalls_idx = self.buffer.find(_DSML_FCALLS_OPEN, search_start)
            dsml_idx = min(
                dsml_invoke_idx if dsml_invoke_idx >= 0 else len(self.buffer),
                dsml_fcalls_idx if dsml_fcalls_idx >= 0 else len(self.buffer),
            )
            if dsml_idx == len(self.buffer):
                dsml_idx = -1

            # If DSML comes before (or instead of) <tool_call, switch to DSML extraction
            if dsml_idx >= 0 and (idx == -1 or dsml_idx < idx):
                if dsml_idx > 0:
                    text = self.buffer[:dsml_idx]
                    self.buffer = self.buffer[dsml_idx:]
                    return {"type": "text", "text": text}
                self.in_tool = True
                return self._try_extract_dsml_tool()

            if idx == -1:
                safe_end = self._safe_text_end()
                if safe_end == 0:
                    return None
                text = self.buffer[:safe_end]
                self.buffer = self.buffer[safe_end:]
                return {"type": "text", "text": text}

            # Validate: real XML tag must be followed by whitespace or >
            # Rejects regex patterns like <tool_call(?:, <tool_call\s+, etc.
            end_of_tag = idx + len(_TOOL_CALL_OPEN)
            if end_of_tag >= len(self.buffer):
                # Buffer ends at "<tool_call" — need more data to decide
                if idx > 0:
                    text = self.buffer[:idx]
                    self.buffer = self.buffer[idx:]
                    return {"type": "text", "text": text}
                return None

            next_char = self.buffer[end_of_tag]
            if next_char == '>':
                # GLM format: <tool_call>ToolName<arg_key>...
                # Validate '>' is followed by alpha char (tool name start).
                # Rejects: `<tool_call>` (backtick-quoted docs), <tool_call>\n, etc.
                name_start = end_of_tag + 1
                if name_start >= len(self.buffer):
                    # Buffer ends at '>' — need more data to check tool name
                    if idx > 0:
                        text = self.buffer[:idx]
                        self.buffer = self.buffer[idx:]
                        return {"type": "text", "text": text}
                    return None
                # Skip optional leading whitespace before tool name.
                # GLM sometimes emits <tool_call>\nToolName\n<arg_key>...
                actual_name_start = name_start
                while actual_name_start < len(self.buffer) and self.buffer[actual_name_start] in ' \t\n\r':
                    actual_name_start += 1
                if actual_name_start >= len(self.buffer):
                    # All whitespace so far — need more data to determine if valid
                    if idx > 0:
                        text = self.buffer[:idx]
                        self.buffer = self.buffer[idx:]
                        return {"type": "text", "text": text}
                    return None
                if not (self.buffer[actual_name_start].isalpha() or
                        self.buffer[actual_name_start] == '_'):
                    # Not a valid tool name start — skip this occurrence.
                    # Digits rejected intentionally: <tool_call>123 is almost certainly docs.
                    # Underscore allowed: MCP tools like _internal_tool are valid.
                    search_start = name_start
                    continue
            elif next_char not in (' ', '\t', '\n', '\r'):
                # Not valid tool_call tag — requires space (name= format)
                search_start = end_of_tag
                continue

            # Skip false positives: <tool_call inside backtick-quoted text (documentation)
            if self._is_backtick_quoted(idx):
                search_start = end_of_tag
                continue

            # Found real <tool_call
            self.in_tool = True
            if idx > 0:
                text = self.buffer[:idx]
                self.buffer = self.buffer[idx:]
                return {"type": "text", "text": text}
            # No text before — go directly to tool extraction
            return self._try_extract_tool()

    _MAX_TOOL_BUFFER = 16_000  # Real tool calls shouldn't exceed this

    def _try_extract_dsml_tool(self) -> dict | None:
        """Try to extract a complete DeepSeek-R1 DSML tool block from buffer start.

        Handles both:
          <｜DSML｜invoke name="X">...</｜DSML｜invoke>
          <｜DSML｜function_calls><｜DSML｜invoke name="X">...</｜DSML｜invoke></｜DSML｜function_calls>
        """
        # Find end of the first complete invoke block
        end_invoke = self.buffer.find(_DSML_INVOKE_CLOSE)
        if end_invoke == -1:
            # Incomplete — need more data
            if len(self.buffer) > self._MAX_TOOL_BUFFER:
                print(f"[xml-buffer] DSML buffer overflow ({len(self.buffer)} chars) — emitting as text")
                text = self.buffer
                self.buffer = ""
                self.in_tool = False
                return {"type": "text", "text": text}
            return None

        end_pos = end_invoke + len(_DSML_INVOKE_CLOSE)

        # Also consume outer function_calls close tag if directly following
        tail = self.buffer[end_pos:].lstrip()
        if tail.startswith(_DSML_FCALLS_CLOSE):
            skip = self.buffer[end_pos:].find(_DSML_FCALLS_CLOSE)
            end_pos += skip + len(_DSML_FCALLS_CLOSE)

        block = self.buffer[:end_pos]
        blocks = _extract_dsml_tool_calls(block, tools=self.tools if hasattr(self, 'tools') else None)
        self.buffer = self.buffer[end_pos:]
        self.in_tool = False

        if blocks:
            b = blocks[0]
            print(f"[xml-buffer] DSML: extracted tool '{b['name']}' keys={list(b['input'].keys())}")
            return {"type": "tool_call", "name": b["name"], "input": b["input"]}

        # Block parsed but no tool found — emit as text
        return {"type": "text", "text": block}

    def _try_extract_tool(self) -> dict | None:
        """Try to extract a complete </tool_call> block, or return None if incomplete.

        v4 algorithm: handles nested </tool_call> inside JSON content (e.g. Write tool
        that contains XML examples). Validates extraction with structural regexes before
        accepting a </tool_call> boundary.
        """
        end_tag = "</tool_call>"
        search_start = 0
        while True:
            end_idx = self.buffer.find(end_tag, search_start)
            if end_idx == -1:
                # No closing tag found — check for buffer overflow
                if len(self.buffer) > self._MAX_TOOL_BUFFER:
                    print(f"[xml-buffer] Buffer overflow ({len(self.buffer)} chars > "
                          f"{self._MAX_TOOL_BUFFER}) — false positive <tool_call>, emitting as text")
                    text = self.buffer
                    self.buffer = ""
                    self.in_tool = False
                    return {"type": "text", "text": text}
                # Double-prefix restart recovery (GLM malformation).
                # Pattern: <tool_call>X<tool_call>RealName<arg_key>...
                # GLM sometimes streams a partial/incorrect name then restarts.
                # Condition: another <tool_call opens BEFORE any arg/input tags -> discard prefix.
                nested_tc_idx = self.buffer.find(_TOOL_CALL_OPEN, 1)
                if nested_tc_idx > 0:
                    # Validate it's a real <tool_call> tag, not a longer variant like
                    # <tool_call_backup> or <tool_call_v2>. The char immediately after
                    # "<tool_call" must be '>' (GLM format) or whitespace (name= format).
                    after_tag_pos = nested_tc_idx + len(_TOOL_CALL_OPEN)
                    if after_tag_pos < len(self.buffer):
                        after_char = self.buffer[after_tag_pos]
                        if after_char not in ('>', ' ', '\t', '\n', '\r'):
                            # Longer tag name — not a real restart signal
                            return None
                    prefix = self.buffer[:nested_tc_idx]
                    has_args = (
                        "<arg_key>" in prefix
                        or "<arg_value>" in prefix
                        or "<input>" in prefix
                        or "<arguments>" in prefix
                        or "<params>" in prefix
                    )
                    if not has_args:
                        print(
                            f"[xml-buffer] double-prefix restart: discarding {len(prefix)} chars "
                            f"({prefix!r}), restarting from nested <tool_call>",
                            flush=True,
                        )
                        self.buffer = self.buffer[nested_tc_idx:]
                        search_start = 0
                        continue  # re-search from the nested <tool_call
                return None  # Need more data

            candidate_end = end_idx + len(end_tag)
            tool_xml = self.buffer[:candidate_end]

            # Fast path: PRIMARY or FALLBACK regex matches (correct <input>...</input> structure)
            if _TOOL_CALL_RE.search(tool_xml) or _TOOL_CALL_FALLBACK_RE.search(tool_xml):
                # Guard: nested <tool_call in JSON content (e.g. Write with XML examples)
                # The non-greedy regex may match inner </input></tool_call> prematurely
                if tool_xml.count(_TOOL_CALL_OPEN) > 1:
                    if end_tag in self.buffer[candidate_end:]:
                        search_start = candidate_end
                        continue
                    # Last </tool_call> but multiple <tool_call — check if some are separate tools
                    real_opens = [m.start() for m in _REAL_NAME_RE.finditer(tool_xml)]
                    if len(real_opens) > 1:
                        # Split: extract first tool only, return rest to buffer
                        split_pos = real_opens[1]
                        first_end = tool_xml.rfind(end_tag, 0, split_pos)
                        if first_end != -1:
                            first_xml = tool_xml[:first_end + len(end_tag)]
                            remaining = tool_xml[first_end + len(end_tag):]
                            self.buffer = remaining + self.buffer[candidate_end:]
                            self.in_tool = False
                            return self._parse_tool_xml(first_xml)
                self.buffer = self.buffer[candidate_end:]
                self.in_tool = False
                return self._parse_tool_xml(tool_xml)

            # More </tool_call> tags in buffer -> the one we found might be inside JSON content
            if end_tag in self.buffer[candidate_end:]:
                search_start = candidate_end
                continue

            # Last </tool_call> — determine if this is the real closing tag
            has_real_name = bool(_REAL_NAME_RE.search(self.buffer[:60]))

            if has_real_name:
                # Real tool call (unescaped quotes in name=) — check for premature extraction
                inner = tool_xml[tool_xml.find('>') + 1:end_idx] if '>' in tool_xml else ""
                if '<input>' in inner and '</input>' not in inner:
                    # Unmatched <input> — outer closing tags haven't arrived yet
                    if len(self.buffer) > self._MAX_TOOL_BUFFER:
                        print(f"[xml-buffer] Buffer overflow ({len(self.buffer)} chars > "
                              f"{self._MAX_TOOL_BUFFER}) — premature </tool_call>, emitting as text")
                        text = self.buffer
                        self.buffer = ""
                        self.in_tool = False
                        return {"type": "text", "text": text}
                    return None  # WAIT for outer closing tag
                # No inner XML tags -> genuine BARE format
                self.buffer = self.buffer[candidate_end:]
                self.in_tool = False
                return self._parse_tool_xml(tool_xml)

            # Escaped name= (backslash-quotes from JSON content) -> parse with normalization
            if '\\"' in tool_xml or '\\n' in tool_xml:
                self.buffer = self.buffer[candidate_end:]
                self.in_tool = False
                return self._parse_tool_xml(tool_xml)  # _parse_tool_xml handles normalization

            # BARE/ARGKV format (no inner tags, no escaping)
            self.buffer = self.buffer[candidate_end:]
            self.in_tool = False
            return self._parse_tool_xml(tool_xml)

    def _is_backtick_quoted(self, idx: int) -> bool:
        """Check if <tool_call at position idx is inside backtick-quoted text."""
        # Check for backtick within 2 chars before (inline code: `<tool_call`)
        nearby = self.buffer[max(0, idx - 2):idx]
        if '`' in nearby:
            return True
        # Check for triple-backtick code block in wider prefix
        # (streaming chunks may split backticks and content across chunks)
        prefix = self.buffer[max(0, idx - 80):idx]
        if '```' in prefix:
            return True
        return False

    def _parse_tool_xml(self, xml: str) -> dict:
        """Parse a complete <tool_call> XML string. Falls back to clean text on parse failure.

        Tries the original XML first, then a normalized (unescaped) version if the XML
        contains JSON-escaped quotes or newlines from content strings.
        """
        print(f"[xml-buffer] _parse_tool_xml: {len(xml)} chars. First 200: {xml[:200]}", flush=True)

        # Build attempt list: original first, then normalized if escaping detected
        normalized = _normalize_escaped_xml(xml)
        attempts = [("orig", xml)]
        if normalized is not None:
            attempts.append(("normalized", normalized))

        for label, attempt_xml in attempts:
            # 1) Primary: known inner tags (<input>, <textarea>, etc.)
            match = _TOOL_CALL_RE.search(attempt_xml)
            if match:
                raw_input = match.group(2)
                # Nested XML in captured content? Use greedy regex to get full outer content.
                _has_embedded_xml = (
                    _TOOL_CALL_OPEN in raw_input
                    or attempt_xml.count('</input>') > 1
                    or attempt_xml.count('</tool_call>') > 1
                )
                if _has_embedded_xml:
                    # Bound greedy search to first tool only (avoid consuming subsequent tools)
                    real_opens = [m.start() for m in _REAL_NAME_RE.finditer(attempt_xml)]
                    if len(real_opens) > 1:
                        boundary = attempt_xml.rfind('</tool_call>', 0, real_opens[1])
                        if boundary != -1:
                            bounded_xml = attempt_xml[:boundary + len('</tool_call>')]
                            greedy_match = _TOOL_CALL_GREEDY_RE.search(bounded_xml)
                        else:
                            greedy_match = _TOOL_CALL_GREEDY_RE.search(attempt_xml)
                    else:
                        greedy_match = _TOOL_CALL_GREEDY_RE.search(attempt_xml)
                    if greedy_match:
                        match = greedy_match
                        raw_input = match.group(2)
                name = match.group(1).strip()
                if self.valid_tool_names and not validate_tool_name(name, self.valid_tool_names):
                    print(f"[xml-buffer] WARNING: Hallucinated tool '{name}' not in valid tools, emitting as text")
                    return {"type": "text", "text": xml}
                parsed = _safe_parse_tool_input(raw_input, name, tools=self.tools)
                tag = f"PRIMARY({label})" if label != "orig" else "PRIMARY"
                print(f"[xml-buffer] {tag} match: name={name} keys={list(parsed.keys())}")
                return {"type": "tool_call", "name": name, "input": parsed}

            # 2) Fallback: any matched pair of XML tags
            fallback_match = _TOOL_CALL_FALLBACK_RE.search(attempt_xml)
            if fallback_match:
                name = fallback_match.group(1).strip()
                if self.valid_tool_names and not validate_tool_name(name, self.valid_tool_names):
                    print(f"[xml-buffer] WARNING: Hallucinated tool '{name}' not in valid tools, emitting as text")
                    return {"type": "text", "text": xml}
                inner_tag = fallback_match.group(2)
                raw_input = fallback_match.group(3)
                tag = f"FALLBACK({label})" if label != "orig" else "FALLBACK"
                print(f"[xml-buffer] {tag} match ({inner_tag}): name={name}")
                parsed = _safe_parse_tool_input(raw_input, name, tools=self.tools)
                return {"type": "tool_call", "name": name, "input": parsed}

            # 3) Bare: no inner tags, JSON directly inside <tool_call>
            bare_match = _TOOL_CALL_BARE_RE.search(attempt_xml)
            if bare_match:
                name = bare_match.group(1).strip()
                if self.valid_tool_names and not validate_tool_name(name, self.valid_tool_names):
                    print(f"[xml-buffer] WARNING: Hallucinated tool '{name}' not in valid tools, emitting as text")
                    return {"type": "text", "text": xml}
                raw_content = bare_match.group(2).strip()
                tag = f"BARE({label})" if label != "orig" else "BARE"
                parsed = _safe_parse_tool_input(raw_content, name, tools=self.tools)
                print(f"[xml-buffer] {tag} match (no inner tags): name={name} content={len(raw_content)} chars keys={list(parsed.keys())}")
                return {"type": "tool_call", "name": name, "input": parsed}

            # 4) GLM format: <tool_call>Name<arg_key>key</arg_key><arg_value>val</arg_value></tool_call>
            argkv_match = _TOOL_CALL_ARGKV_RE.search(attempt_xml)
            if argkv_match:
                parsed = _parse_argkv_tool(argkv_match)
                name = parsed["name"]
                if self.valid_tool_names and not validate_tool_name(name, self.valid_tool_names):
                    print(f"[xml-buffer] WARNING: Hallucinated tool '{name}' not in valid tools, emitting as text")
                    return {"type": "text", "text": xml}
                tag = f"ARGKV({label})" if label != "orig" else "ARGKV"
                print(f"[xml-buffer] {tag} match: name={name} keys={list(parsed['input'].keys())}")
                return {"type": "tool_call", "name": name, "input": parsed["input"]}

        # All regexes failed on all attempts — emit as CLEAN text (strip XML tags)
        clean = strip_tool_call_xml(xml)
        print(f"[xml-buffer] FAILED all regexes ({len(xml)} chars), emitting {len(clean)} chars clean text. "
              f"First 200: {xml[:200]}", flush=True)
        return {"type": "text", "text": clean or ""}

    def _safe_text_end(self) -> int:
        """Find safe end position, avoiding partial '<tool_call' or DSML matches at buffer end."""
        for i in range(1, min(len(_TOOL_CALL_OPEN), len(self.buffer)) + 1):
            if _TOOL_CALL_OPEN.startswith(self.buffer[-i:]):
                return len(self.buffer) - i
        # Also protect partial DSML open markers: <｜DSML｜invoke  and  <｜DSML｜function_calls
        for marker in (_DSML_INVOKE_OPEN, _DSML_FCALLS_OPEN):
            for i in range(1, min(len(marker), len(self.buffer)) + 1):
                if marker.startswith(self.buffer[-i:]):
                    return len(self.buffer) - i
        return len(self.buffer)


# ---------------------------------------------------------------------------
# Universal Tool Extraction Transformer (RESPONSE transformer)
# ---------------------------------------------------------------------------

class UniversalToolExtractionTransformer(Transformer):
    """
    AGNOSTIC RESPONSE transformer for universal tool extraction from ALL model outputs.

    Processes ALL response types from model: thinking, content, tool_calls, mixed
    Extracts tools from ANY format: XML, JSON, native tool_use blocks, text descriptions
    Applies to ALL models: no-tools, native tools, mixed (NO classification dependency)

    AGNOSTIC DESIGN REQUIREMENT:
    - Zero model-specific if/elif blocks (no model_name checks)
    - Zero hardcoded model patterns (no "deepseek-reasoner", "r1", "glm", "minimax" checks)
    - Same behavior for ALL models
    - Future-proof: New models automatically supported

    This replaces scattered model-specific tool extraction logic in tool_prompting.py
    with a centralized, AGNOSTIC universal extraction system that's easy to maintain.

    TRANSFORMER TYPE: RESPONSE (processes model OUTPUT, not input)
    """

    @property
    def name(self) -> str:
        return "universal_tool_extraction"

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    async def transform(self, request: object, ctx: TransformContext) -> None:
        """
        Extract tools from model output universally.

        AGNOSTIC: Applies to ALL models, ALL output types, NO model-specific logic.

        This is a RESPONSE transformer - it processes model OUTPUT, not input.
        Runs AFTER Anthropic/LiteLLM endpoint returns, BEFORE client response.

        Process ALL response types:
        - thinking content (reasoning_content)
        - text content (content blocks)
        - native tool calls (tool_use blocks)
        - mixed responses (text + tools + thinking)

        Extract tools from ANY format:
        - XML tags: <tool_call><tool_name>...</tool_name></tool_call>
        - JSON format: {"tool": "...", "arguments": {...}}
        - Native tool_use blocks (already structured)
        - Text descriptions: "Voy a escribir el reporte..." → extract XML within text

        Returns None (modifies request/response in-place by adding extracted tools).
        """
        if not self.enabled:
            return

        # Convert dict/Pydantic responses to mutable SimpleNamespace for attribute access.
        # Pydantic MessagesResponse forbids arbitrary field assignment; SimpleNamespace
        # allows free mutation. Note: content list mutations propagate via reference.
        request = _ensure_request_object(request)

        # Skip if no tools were requested in original request
        # AGNOSTIC: No model-specific checks, just verify tools were defined
        # Use ctx.tools instead of request.tools (response objects don't have tools)
        if not ctx.tools:
            logger.debug(
                f"[universal-tool-extraction] Skipping - no tools in context"
            )
            return

        # Use ctx for tool call storage (avoids Pydantic field assignment errors)
        ctx.extracted_tool_calls = []
        ctx.xml_tool_buffer = None

        # Process ALL response types from model output
        # AGNOSTIC: No model-specific checks, process everything uniformly

        # 1. Process thinking/reasoning content if present
        reasoning_content = getattr(request, "reasoning_content", None)
        if reasoning_content:
            await self._extract_tools_from_reasoning(request, reasoning_content, ctx)

        # 2. Process text content blocks
        text_content = self._extract_text_content(request)
        if text_content:
            await self._extract_tools_from_text(request, text_content, ctx)

        # 3. Process native tool_use blocks (already structured)
        await self._extract_native_tools(request, ctx)

        # 4. Apply extracted XML tool calls back to response content
        await self._apply_extracted_tools_to_content(request, ctx)

        # 5. Log extraction results
        extracted_count = len(ctx.extracted_tool_calls)
        if extracted_count > 0:
            logger.info(
                f"[universal-tool-extraction] Extracted {extracted_count} tool(s) from model output (AGNOSTIC, no model-specific routing)"
            )
        else:
            logger.debug(
                f"[universal-tool-extraction] No tools extracted from model output"
            )

        return None

    async def _extract_tools_from_reasoning(self, request: object, reasoning_content: str, ctx: Any) -> None:
        """
        Extract tools from reasoning/thinking content.

        AGNOSTIC: Works for ALL models, no model-specific checks.
        """
        if not reasoning_content:
            return

        # Strip reasoning tags if present (<reasoning>...</reasoning>)
        clean_reasoning = reasoning_content.strip()
        if clean_reasoning.startswith("<reasoning>") and clean_reasoning.endswith("</reasoning>"):
            clean_reasoning = clean_reasoning[11:-12].strip()

        if not clean_reasoning:
            return

        # Extract XML tool calls from reasoning content using universal extraction
        try:
            tools = getattr(request, "tools", None)
            valid_tool_names = _build_valid_tool_names(tools) if tools else set()
            tool_calls, _ = extract_tool_calls_from_text(
                clean_reasoning, valid_tool_names=valid_tool_names, tools=tools
            )

            if tool_calls:
                logger.debug(
                    f"[universal-tool-extraction] Extracted {len(tool_calls)} tool(s) from reasoning content ({len(clean_reasoning)} chars)"
                )
                for tool_call in tool_calls:
                    self._add_tool_call(ctx, tool_call)
        except Exception as e:
            logger.warning(
                f"[universal-tool-extraction] Error extracting tools from reasoning: {e}"
            )

    async def _extract_tools_from_text(self, request: object, text_content: str, ctx: Any) -> None:
        """
        Extract tools from text content.

        AGNOSTIC: Works for ALL models, no model-specific checks.
        """
        if not text_content:
            return

        # Get valid tool names from request tools
        tools = getattr(request, "tools", None)
        valid_tool_names = set()
        if tools:
            valid_tool_names = _build_valid_tool_names(tools)

        # Extract XML tool calls from text
        # AGNOSTIC: Universal extraction works for all XML formats
        try:
            tool_calls, remaining_text = extract_tool_calls_from_text(
                text_content, valid_tool_names=valid_tool_names, tools=tools
            )

            if tool_calls:
                logger.debug(
                    f"[universal-tool-extraction] Extracted {len(tool_calls)} tool(s) from text content ({len(text_content)} chars)"
                )

                # Track XML extraction metrics
                metrics.increment_tool_counter("xml_extracted")

                # Add all extracted tools to ctx
                for tool_call in tool_calls:
                    self._add_tool_call(ctx, tool_call)

            # CRITICAL FIX: Clean remaining text to remove orphaned XML tags
            # This prevents XML artifacts from appearing in user-facing text
            if remaining_text:
                clean_remaining = strip_tool_call_xml(remaining_text)
                if clean_remaining != remaining_text:
                    logger.info(
                        f"[universal-tool-extraction] Cleaned orphaned XML tags from remaining text "
                        f"({len(remaining_text)} -> {len(clean_remaining)} chars)"
                    )
                # Update text content in request with cleaned text
                # This ensures user-facing text is free of XML artifacts
                await self._update_text_content(request, text_content, clean_remaining)

        except Exception as e:
            logger.warning(
                f"[universal-tool-extraction] Error extracting tools from text: {e}"
            )

    async def _extract_native_tools(self, request: object, ctx: Any) -> None:
        """
        Process native tool_use blocks from model output.

        AGNOSTIC: Works for ALL models, no model-specific checks.
        """
        # Check if response has native tool_use blocks
        if not hasattr(request, "content"):
            return

        content = getattr(request, "content", [])
        if not content:
            return

        # Extract native tool_use blocks (handle both dict and Pydantic objects)
        tool_calls = []
        for block in content:
            if _get_block_type(block) == "tool_use":
                tool_calls.append(block)

        if tool_calls:
            logger.debug(
                f"[universal-tool-extraction] Found {len(tool_calls)} native tool_use block(s)"
            )

            # Track native tool metrics
            metrics.increment_tool_counter("native")

            # Add native tools to extracted tools
            for tool_call in tool_calls:
                self._add_tool_call(ctx, tool_call)

    def _extract_text_content(self, request: object) -> str:
        """
        Extract concatenated text content from response.

        AGNOSTIC: Works for ALL models, no model-specific checks.
        """
        if not hasattr(request, "content"):
            return ""

        content = getattr(request, "content", [])
        if not content:
            return ""

        # Extract text from content blocks (handle both dict and Pydantic objects)
        text_parts = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif _get_block_type(block) == "text":
                text_parts.append(_get_block_text(block))

        return "\n".join(text_parts)

    async def _update_text_content(self, request: object, original_text: str, cleaned_text: str) -> None:
        """
        Update text content blocks in request with cleaned text.

        Replaces original text content with cleaned text to remove XML artifacts.
        AGNOSTIC: Works for ALL models, no model-specific logic.
        """
        if not hasattr(request, "content"):
            return

        content = getattr(request, "content", [])
        if not content:
            return

        # Find and replace text blocks in-place (avoids Pydantic content reassignment)
        for i, block in enumerate(content):
            if _get_block_type(block) == "text":
                block_text = _get_block_text(block)
                if block_text == original_text:
                    logger.debug(
                        f"[universal-tool-extraction] Updated text block with cleaned content "
                        f"({len(block_text)} -> {len(cleaned_text)} chars)"
                    )
                    if isinstance(block, dict):
                        content[i] = {"type": "text", "text": cleaned_text}
                    else:
                        # Pydantic field mutation (content list is mutable)
                        block.text = cleaned_text
                    break

    def _add_tool_call(self, ctx: Any, tool_call: dict) -> None:
        """
        Add a tool call to ctx.extracted_tool_calls, avoiding duplicates.

        AGNOSTIC: No model-specific logic, just deduplication.
        """
        # Check if this tool call is already in list
        # Use tool_id or name + input signature as unique identifier
        tool_id = tool_call.get("id") or tool_call.get("name", "")
        if not tool_id:
            return  # Skip invalid tool calls

        # Check for duplicates
        for existing in ctx.extracted_tool_calls:
            existing_id = existing.get("id") or existing.get("name", "")
            if existing_id == tool_id:
                return  # Same tool ID - skip duplicate

        ctx.extracted_tool_calls.append(tool_call)

        logger.debug(
            f"[universal-tool-extraction] Added tool: {tool_call.get('name')} (id: {tool_id})"
        )

    async def _apply_extracted_tools_to_content(self, request: object, ctx: Any) -> None:
        """Add XML-extracted tool calls back to response content as tool_use blocks.

        Native tool_use blocks (already in content) are skipped to avoid duplicates.
        This is the final step that ensures extracted tools are actually returned to
        the client — without this, extracted tools would be silently discarded.
        """
        if not ctx.extracted_tool_calls:
            return

        content = getattr(request, "content", None)
        if content is None:
            return

        # Build set of IDs already present as tool_use blocks
        existing_ids: set = set()
        for block in content:
            if _get_block_type(block) == "tool_use":
                block_id = (
                    block.get("id") if isinstance(block, dict) else getattr(block, "id", None)
                )
                block_name = (
                    block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
                )
                existing_ids.add(block_id or block_name)

        added = 0
        for tool_call in ctx.extracted_tool_calls:
            tool_id = tool_call.get("id") or tool_call.get("name")
            if tool_id not in existing_ids:
                content.append(tool_call)
                existing_ids.add(tool_id)
                added += 1

        if added:
            logger.info(
                f"[universal-tool-extraction] Applied {added} extracted tool(s) to response content"
            )

    # ── Legacy Support Methods ────────────────────────────────────

    def extract_from_anthropic_response(self, response: dict, request: object) -> List[Dict]:
        """
        Extract tools from a complete Anthropic-format response (non-streaming).

        AGNOSTIC: Works for ALL models, no model-specific logic.

        This method provides backward compatibility with existing extraction logic
        while also supporting new streaming infrastructure.
        """
        if not self.enabled:
            return []

        # Process complete response using existing logic
        # This delegates to main transform() method internally

        # Temporarily attach response to request for processing
        original_content = getattr(request, "content", None)
        request.content = response.get("content", [])

        # Call main extraction logic
        # Note: This doesn't use async/await properly but maintains compatibility
        extracted_tools = getattr(request, "extracted_tool_calls", [])

        # Restore original content
        if original_content is not None:
            request.content = original_content

        return extracted_tools


# ── No-tools model prompt builders (migrated from llm/converters.py) ─────────

def _format_schema_properties(input_schema: dict, depth: int = 0, max_depth: int = 2) -> str:
    """Format JSON Schema properties into readable parameter list."""
    props = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    indent = "  " * (depth + 1)
    if not props:
        return f"{indent}(no parameters)"
    lines = []
    for name, prop in props.items():
        ptype = prop.get("type", "any")
        desc = prop.get("description", "")
        if len(desc) > 120:
            desc = desc[:117] + "..."
        req = "required" if name in required else "optional"
        line = f"{indent}- {name} ({ptype}, {req})"
        if desc:
            line += f": {desc}"
        enum_vals = prop.get("enum")
        if enum_vals and isinstance(enum_vals, list):
            line += f" [values: {', '.join(str(v) for v in enum_vals)}]"
        lines.append(line)
        if ptype == "array" and depth < max_depth:
            items = prop.get("items", {})
            if isinstance(items, dict) and items.get("properties"):
                lines.append(f"{indent}  Each item (object):")
                lines.append(_format_schema_properties(items, depth=depth + 1, max_depth=max_depth))
            elif isinstance(items, dict):
                item_type = items.get("type", "any")
                item_enum = items.get("enum")
                if item_enum:
                    lines.append(f"{indent}  Items: {item_type} [values: {', '.join(str(v) for v in item_enum)}]")
        elif ptype == "object" and depth < max_depth and prop.get("properties"):
            lines.append(_format_schema_properties(prop, depth=depth + 1, max_depth=max_depth))
    return "\n".join(lines)


def _build_tool_quick_reference(tools: list) -> str:
    """Build compact reference: ToolName(param, param?)"""
    lines = []
    for tool in tools:
        name = tool.get("name", "unknown")
        schema = tool.get("input_schema", {}) or {}
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        params = []
        for prop_name, prop_schema in props.items():
            suffix = "" if prop_name in required else "?"
            ptype = prop_schema.get("type", "any") if isinstance(prop_schema, dict) else "any"
            if ptype == "array" and isinstance(prop_schema, dict):
                items = prop_schema.get("items", {})
                if isinstance(items, dict) and items.get("properties"):
                    inner_parts = []
                    inner_req = set(items.get("required", []))
                    for iname, ischema in items["properties"].items():
                        isuffix = "" if iname in inner_req else "?"
                        ienum = ischema.get("enum") if isinstance(ischema, dict) else None
                        if ienum and isinstance(ienum, list):
                            inner_parts.append(f"{iname}{isuffix}({'/'.join(str(v) for v in ienum)})")
                        else:
                            inner_parts.append(f"{iname}{isuffix}")
                    params.append(f"{prop_name}{suffix}=[{{{', '.join(inner_parts)}}}]")
                else:
                    params.append(f"{prop_name}{suffix}")
            else:
                enum_vals = prop_schema.get("enum") if isinstance(prop_schema, dict) else None
                if enum_vals and isinstance(enum_vals, list):
                    params.append(f"{prop_name}{suffix}({'/'.join(str(v) for v in enum_vals)})")
                else:
                    params.append(f"{prop_name}{suffix}")
        lines.append(f"- {name}({', '.join(params)})" if params else f"- {name}()")
    return "\n".join(lines)


_FEW_SHOT_EXAMPLES: dict = {
    "Read": {"file_path": "/src/main.py"},
    "Write": {"file_path": "/src/main.py", "content": "print('hello')"},
    "Edit": {"file_path": "/src/main.py", "old_string": "hello", "new_string": "world"},
    "Bash": {"command": "ls -la", "description": "List files"},
    "Grep": {"pattern": "def main", "path": "/src"},
    "Glob": {"pattern": "**/*.py"},
    "TodoWrite": {"todos": [{"content": "Fix bug", "status": "in_progress", "activeForm": "Fixing bug"}]},
    "Agent": {"description": "Search codebase", "prompt": "Find all API endpoints", "subagent_type": "Explore"},
    "AskUserQuestion": {"questions": [{"question": "Which approach?", "header": "Approach", "options": [{"label": "A", "description": "First"}, {"label": "B", "description": "Second"}], "multiSelect": False}]},
    "EnterPlanMode": {},
    "ExitPlanMode": {},
    "WebSearch": {"query": "python async best practices 2025"},
    "WebFetch": {"url": "https://example.com/docs", "prompt": "Extract the API reference"},
    "Skill": {"skill": "commit"},
    "NotebookEdit": {"notebook_path": "/notebooks/analysis.ipynb", "new_source": "print('hello')", "cell_type": "code", "edit_mode": "replace"},
}


def _build_few_shot_examples(tools: list) -> str:
    """Build few-shot examples for CC core tools present in the request."""
    valid_names = _build_valid_tool_names(tools)
    lines = [
        "EXAMPLES (follow this EXACT format for EVERY tool call — "
        "parameters are ALWAYS a JSON object inside <input> tags):\n"
    ]
    found = False
    for name, example_input in _FEW_SHOT_EXAMPLES.items():
        if name in valid_names:
            found = True
            lines.append(f'<tool_call name="{name}">')
            lines.append("<input>")
            lines.append(json.dumps(example_input))
            lines.append("</input>")
            lines.append("</tool_call>\n")
    if not found:
        lines.append('<tool_call name="ToolName">')
        lines.append("<input>")
        lines.append('{"param1": "value1", "param2": "value2"}')
        lines.append("</input>")
        lines.append("</tool_call>\n")
    lines.append(
        "WRONG FORMAT (NEVER do this — parameters are NOT XML tags):\n"
        '<tool_call name="Read">\n'
        "<file_path>/path/to/file.py</file_path>\n"
        "</tool_call>\n\n"
        "WRONG FORMAT (NEVER do this — do NOT use single quotes):\n"
        "<tool_call name='Read'>\n"
        "<input>{'file_path': '/path/to/file.py'}</input>\n"
        "</tool_call>\n\n"
        'WRONG FORMAT (NEVER do this — do NOT use <parameter> attributed tags):\n'
        '<tool_call name="Read">\n'
        '<parameter name="file_path">/path/to/file.py</parameter>\n'
        "</tool_call>\n\n"
    )
    return "\n".join(lines)


def build_tool_prompt(tools: list) -> str:
    """Convert Anthropic tool definitions to an XML-format prompt for no-tools models."""
    header = (
        "You have access to the following tools. "
        "When you need to use a tool, you MUST respond using this EXACT XML format:\n\n"
        '<tool_call name="tool_name">\n'
        "<input>\n"
        '{"param1": "value1", "param2": "value2"}\n'
        "</input>\n"
        "</tool_call>\n\n"
        "RULES:\n"
        '- CRITICAL: You MUST use exactly <input> and </input> tags. Do NOT use <textarea>, <arguments>, <params>, or any other tag name.\n'
        '- CRITICAL: Tool parameters MUST be a JSON object inside <input> tags. '
        'NEVER use XML tags for parameters (e.g., <file_path>, <content>, <command>). '
        'Do NOT use <parameter name="X">value</parameter> format either.\n'
        '- CRITICAL: Use DOUBLE QUOTES for the name attribute: name="ToolName" (NOT name=\'ToolName\').\n'
        '- CRITICAL: The <input> must contain valid JSON (double quotes for keys and string values, NOT single quotes).\n'
        '- CRITICAL: Do NOT include <reasoning> tags or any non-tool XML inside <tool_call> blocks. Put reasoning OUTSIDE the tool call.\n'
        '- CRITICAL: Do NOT invent XML tag names like <tool_name>, <args>, <function>. Use ONLY <tool_call> and <input>.\n'
        "- You can include text before and after tool calls.\n"
        "- You can make multiple tool calls in a single response.\n"
        "- Always use the exact tool name as listed below.\n"
        "- Do NOT nest tool calls inside other tool calls.\n"
        "- NEVER describe what tool you would use in text. ALWAYS output the <tool_call> XML directly.\n"
        "- Do NOT say 'I will use the Read tool' or 'Let me run a command'. Instead, directly output the XML.\n\n"
    )
    tool_names = sorted(_build_valid_tool_names(tools))
    if tool_names:
        header += (
            "VALID TOOL NAMES (use ONLY these, NEVER invent others):\n"
            + ", ".join(tool_names) + "\n"
            "If a tool you need is not in this list, explain what you need — do NOT fabricate a tool name.\n\n"
        )
    header += _build_few_shot_examples(tools)
    if tools:
        header += (
            "TOOL QUICK REFERENCE (all available tools with their parameters):\n"
            + _build_tool_quick_reference(tools) + "\n"
        )
    tool_sections = []
    for tool in tools:
        name = tool.get("name", "unknown")
        desc = tool.get("description", "") or ""
        if len(desc) > 200:
            desc = desc[:197] + "..."
        schema = tool.get("input_schema", {}) or {}
        params = _format_schema_properties(schema)
        tool_sections.append(f"### {name}\n{desc}\nParameters:\n{params}")
    return header + "\n## Available Tools\n\n" + "\n\n".join(tool_sections)


def rewrite_messages_without_tools(messages: list) -> list:
    """
    Post-process OpenAI-format messages to remove native tool constructs.

    - Assistant messages with tool_calls -> assistant text with XML
    - role:"tool" messages -> user text with <tool_result> XML
    - Merges consecutive same-role messages
    """
    rewritten: list = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "assistant" and "tool_calls" in msg:
            text_parts = []
            content = msg.get("content")
            if content:
                text_parts.append(str(content))
            for tc in msg.get("tool_calls", []):
                func = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = func.get("name", "unknown")
                args = func.get("arguments", "{}")
                text_parts.append(
                    f'<tool_call name="{name}">\n<input>\n{args}\n</input>\n</tool_call>'
                )
            rewritten.append({"role": "assistant", "content": "\n\n".join(text_parts)})
        elif role == "tool":
            tool_id = msg.get("tool_call_id", "unknown")
            content = msg.get("content", "")
            rewritten.append({
                "role": "user",
                "content": f'<tool_result tool_use_id="{tool_id}">\n{content}\n</tool_result>',
            })
        else:
            rewritten.append(msg.copy())
    # Merge consecutive same-role messages to avoid API errors
    if not rewritten:
        return rewritten
    merged: list = [rewritten[0]]
    for msg in rewritten[1:]:
        if msg["role"] == merged[-1]["role"]:
            prev_content = merged[-1].get("content", "") or ""
            new_content = msg.get("content", "") or ""
            merged[-1]["content"] = f"{prev_content}\n\n{new_content}".strip()
        else:
            merged.append(msg)
    return merged


# ── Passthrough non-streaming XML tool extraction (migrated from llm/converters.py) ─

def extract_xml_tools_from_passthrough_response(response: dict, request: Any) -> dict:
    """Extract <tool_call> XML from passthrough non-streaming Anthropic-format response.

    The passthrough client (PassthroughClient.create_message) returns an Anthropic-
    format dict directly. When GLM-4.7 embeds tool calls as XML text in the content
    blocks, this function extracts them into proper tool_use blocks — mirroring what
    convert_litellm_to_anthropic() already does for the LiteLLM non-streaming path.

    Returns the original dict unchanged if no XML tool calls are found.
    """
    content = response.get("content")
    if not content:
        return response

    request_tools = getattr(request, "tools", None)
    if not request_tools:
        return response

    valid_names = _build_valid_tool_names(request_tools)
    new_content: list = []
    extracted_any = False

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            new_content.append(block)
            continue
        text = block.get("text", "")
        if "<tool_call" not in text:
            new_content.append(block)
            continue

        tool_blocks, clean_text = extract_tool_calls_from_text(
            text, valid_tool_names=valid_names, tools=request_tools
        )
        if tool_blocks:
            extracted_any = True
            clean_text = clean_text.strip()
            if clean_text:
                new_content.append({"type": "text", "text": clean_text})
            new_content.extend(tool_blocks)
        else:
            new_content.append(block)

    if not extracted_any:
        return response

    result = dict(response)
    result["content"] = new_content
    result["stop_reason"] = "tool_use"
    print(
        f"[passthrough-xml] non-stream: extracted {sum(1 for b in new_content if isinstance(b, dict) and b.get('type') == 'tool_use')} tool(s) from text content",
        flush=True,
    )
    return result
