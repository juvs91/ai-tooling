# llm/tool_prompting.py
"""
Tool simulation via XML prompting for models without native function calling.

When a model is in NO_TOOLS_MODELS (env var), the proxy:
  REQUEST:  strips tools/tool_choice, injects tool definitions as XML prompt,
            rewrites message history (tool_use → XML text, tool_result → XML text)
  RESPONSE: parses XML <tool_call> tags from text, converts to Anthropic tool_use blocks
"""
from __future__ import annotations

import json
import os
import re
import uuid
from functools import lru_cache
from typing import Any, FrozenSet

from json_repair import repair_json


# ---------------------------------------------------------------------------
# 1. Model detection
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_no_tools_models() -> FrozenSet[str]:
    """Load and validate NO_TOOLS_MODELS from env. Cached via lru_cache(1)."""
    raw = os.environ.get("NO_TOOLS_MODELS", "").strip()
    if not raw:
        return frozenset()

    models = frozenset(
        m.strip().lower()
        for m in raw.split(",")
        if m.strip() and len(m.strip()) > 2
    )
    if models:
        print(f"[no-tools] Loaded NO_TOOLS_MODELS: {', '.join(sorted(models))}")
    return models


def is_no_tools_model(model: str) -> bool:
    """Check if model matches any pattern in NO_TOOLS_MODELS."""
    patterns = _load_no_tools_models()
    if not patterns:
        return False
    model_lower = model.lower()
    return any(pattern in model_lower for pattern in patterns)


# ---------------------------------------------------------------------------
# 2. Tool prompt builder
# ---------------------------------------------------------------------------

def _format_schema_properties(input_schema: dict) -> str:
    """Format JSON Schema properties into readable parameter list."""
    props = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    if not props:
        return "  (no parameters)"

    lines = []
    for name, prop in props.items():
        ptype = prop.get("type", "any")
        desc = prop.get("description", "")
        if len(desc) > 120:
            desc = desc[:117] + "..."
        req = "required" if name in required else "optional"
        line = f"  - {name} ({ptype}, {req})"
        if desc:
            line += f": {desc}"
        lines.append(line)
    return "\n".join(lines)


def build_tool_prompt(tools: list[dict]) -> str:
    """
    Convert Anthropic tool definitions to an XML-format prompt.

    Args:
        tools: list of dicts with keys: name, description, input_schema
    Returns:
        Prompt string with tool definitions and XML format instructions.
    """
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
        '- CRITICAL: Use DOUBLE QUOTES for the name attribute: name="ToolName" (NOT name=\'ToolName\').\n'
        '- CRITICAL: The <input> must contain valid JSON (double quotes for keys and string values, NOT single quotes).\n'
        "- You can include text before and after tool calls.\n"
        "- You can make multiple tool calls in a single response.\n"
        "- Always use the exact tool name as listed below.\n"
        "- Do NOT nest tool calls inside other tool calls.\n"
        "- NEVER describe what tool you would use in text. ALWAYS output the <tool_call> XML directly.\n"
        "- Do NOT say 'I will use the Read tool' or 'Let me run a command'. Instead, directly output the XML.\n\n"
        "EXAMPLES:\n"
        'To read a file:\n'
        '<tool_call name="Read">\n'
        '<input>\n'
        '{"file_path": "/path/to/file.py"}\n'
        '</input>\n'
        '</tool_call>\n\n'
        'To run a command:\n'
        '<tool_call name="Bash">\n'
        '<input>\n'
        '{"command": "ls -la", "description": "List files"}\n'
        '</input>\n'
        '</tool_call>\n\n'
        'To search for files:\n'
        '<tool_call name="Glob">\n'
        '<input>\n'
        '{"pattern": "**/*.py"}\n'
        '</input>\n'
        '</tool_call>\n'
    )

    tool_sections = []
    for tool in tools:
        name = tool.get("name", "unknown")
        desc = tool.get("description", "") or ""
        if len(desc) > 200:
            desc = desc[:197] + "..."
        schema = tool.get("input_schema", {}) or {}
        params = _format_schema_properties(schema)
        section = f"### {name}\n{desc}\nParameters:\n{params}"
        tool_sections.append(section)

    return header + "\n## Available Tools\n\n" + "\n\n".join(tool_sections)


# ---------------------------------------------------------------------------
# 3. Message history rewriter
# ---------------------------------------------------------------------------

def _merge_consecutive_messages(messages: list[dict]) -> list[dict]:
    """Merge consecutive messages with the same role to avoid API errors."""
    if not messages:
        return messages

    merged: list[dict] = [messages[0].copy()]
    for msg in messages[1:]:
        if msg["role"] == merged[-1]["role"]:
            prev_content = merged[-1].get("content", "") or ""
            new_content = msg.get("content", "") or ""
            merged[-1]["content"] = f"{prev_content}\n\n{new_content}".strip()
        else:
            merged.append(msg.copy())
    return merged


def rewrite_messages_without_tools(messages: list[dict]) -> list[dict]:
    """
    Post-process OpenAI-format messages to remove native tool constructs.

    - Assistant messages with tool_calls → assistant text with XML
    - role:"tool" messages → user text with <tool_result> XML
    - Merges consecutive same-role messages
    """
    rewritten: list[dict] = []

    for msg in messages:
        role = msg.get("role", "")

        if role == "assistant" and "tool_calls" in msg:
            # Convert tool_calls to XML text
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

            rewritten.append({
                "role": "assistant",
                "content": "\n\n".join(text_parts),
            })

        elif role == "tool":
            # Convert tool result to XML text as user message
            tool_id = msg.get("tool_call_id", "unknown")
            content = msg.get("content", "")
            rewritten.append({
                "role": "user",
                "content": f'<tool_result tool_use_id="{tool_id}">\n{content}\n</tool_result>',
            })

        else:
            rewritten.append(msg.copy())

    return _merge_consecutive_messages(rewritten)


# ---------------------------------------------------------------------------
# 4. Response parser
# ---------------------------------------------------------------------------

# Primary regex: matches known inner-tag variants models may use
# Accept both single and double quotes for name= attribute (deepseek-reasoner uses single quotes)
_INNER_TAG = r"(?:input|textarea|arguments|params|json|content|parameters)"
_NAME_ATTR = r"""name=["']([^"']+)["']"""
_TOOL_CALL_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*<{_INNER_TAG}>([\s\S]*?)</{_INNER_TAG}>\s*</tool_call>',
    re.DOTALL,
)
# Fallback regex: matches any single XML tag wrapping the content
_TOOL_CALL_FALLBACK_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*<(\w+)>([\s\S]*?)</\2>\s*</tool_call>',
    re.DOTALL,
)
# Last-resort regex: NO inner tags — JSON directly inside <tool_call>
# Handles: <tool_call name="Read">{"file_path": "/path"}</tool_call>
_TOOL_CALL_BARE_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*([\s\S]*?)\s*</tool_call>',
    re.DOTALL,
)


def _strip_inner_xml_tags(raw: str) -> str:
    """
    Strip wrapping XML inner tags if present.
    Handles: <input>{json}</input> → {json}
             <textarea>{json}</textarea> → {json}
             {json} → {json} (no-op)
    """
    stripped = raw.strip()
    # Try to remove a matched pair of XML tags wrapping the content
    tag_match = re.match(r'^<(\w+)>([\s\S]*)</\1>$', stripped, re.DOTALL)
    if tag_match:
        return tag_match.group(2).strip()
    return stripped


def _safe_parse_tool_input(raw_input: str, tool_name: str) -> dict:
    """
    Parse tool input JSON with multiple fallback strategies.
    NEVER raises — always returns a valid dict.
    """
    raw = raw_input.strip()
    if not raw:
        return {}

    # 0) Strip any wrapping XML tags (e.g. <input>...</input>)
    raw = _strip_inner_xml_tags(raw)

    # 1) Direct JSON parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except json.JSONDecodeError:
        pass

    # 2) json_repair
    try:
        repaired = repair_json(raw, return_objects=True)
        if isinstance(repaired, dict):
            print(f"[no-tools] Repaired malformed JSON for tool '{tool_name}'")
            return repaired
        return {"value": repaired}
    except Exception:
        pass

    # 3) Last resort: wrap raw string
    print(f"[no-tools] Could not parse tool input for '{tool_name}', wrapping as raw")
    return {"raw_input": raw}


def extract_tool_calls_from_text(text: str) -> tuple[list[dict], str]:
    """
    Extract XML tool calls from text response.

    Returns:
        (tool_call_blocks, remaining_text)
        - tool_call_blocks: list of Anthropic tool_use dicts
        - remaining_text: text with tool_call XML removed

    Resilience guarantees:
        - Malformed JSON → repaired or wrapped as {"raw_input": ...}
        - Invalid XML structure → ignored (stays as text)
        - Empty input → empty dict {}
        - Tolerates model using wrong inner tags (textarea, arguments, etc.)
        - Never raises exceptions
    """
    if not text:
        return [], ""

    tool_blocks: list[dict] = []
    used_re = _TOOL_CALL_RE
    try:
        for match in _TOOL_CALL_RE.finditer(text):
            name = match.group(1).strip()
            raw_input = match.group(2)
            parsed_input = _safe_parse_tool_input(raw_input, name)
            tool_blocks.append({
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": name,
                "input": parsed_input,
            })

        # Fallback: try permissive regex if primary found nothing
        if not tool_blocks and "<tool_call" in text:
            for match in _TOOL_CALL_FALLBACK_RE.finditer(text):
                name = match.group(1).strip()
                inner_tag = match.group(2)
                raw_input = match.group(3)
                print(f"[no-tools] WARNING: Model used <{inner_tag}> instead of <input> for tool '{name}' — parsed via fallback regex")
                parsed_input = _safe_parse_tool_input(raw_input, name)
                tool_blocks.append({
                    "type": "tool_use",
                    "id": f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": name,
                    "input": parsed_input,
                })
            used_re = _TOOL_CALL_FALLBACK_RE

        # 3rd fallback: bare regex (no inner tags at all)
        # Also handles tool calls with NO input (e.g. EnterPlanMode, ExitPlanMode)
        if not tool_blocks and "<tool_call" in text:
            for match in _TOOL_CALL_BARE_RE.finditer(text):
                name = match.group(1).strip()
                raw_content = match.group(2).strip()
                print(f"[no-tools] BARE regex match for tool '{name}' (no inner tags, content={len(raw_content)} chars)")
                parsed_input = _safe_parse_tool_input(raw_content, name)
                tool_blocks.append({
                    "type": "tool_use",
                    "id": f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": name,
                    "input": parsed_input,
                })
            used_re = _TOOL_CALL_BARE_RE

    except Exception as e:
        print(f"[no-tools] Error extracting tool calls: {e}")
        return [], text

    if not tool_blocks:
        if "<tool_call" in text:
            print(f"[no-tools] WARNING: Found <tool_call> in text but ALL regexes failed. First 500 chars: {text[:500]}")
        return [], text

    remaining = used_re.sub("", text).strip()
    return tool_blocks, remaining


# ---------------------------------------------------------------------------
# 5. Recovery for truncated tool calls
# ---------------------------------------------------------------------------

# Regex to extract partial tool call: name + whatever JSON we got
_PARTIAL_TOOL_RE = re.compile(
    r'<tool_call\s+' + _NAME_ATTR + r'\s*>\s*<' + _INNER_TAG + r'>\s*([\s\S]*)',
    re.DOTALL,
)


def _get_tool_required_fields(tool_name: str, tools: list | None) -> set[str]:
    """Get required fields from a tool's input_schema."""
    if not tools:
        return set()
    for t in tools:
        name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
        if name == tool_name:
            schema = getattr(t, "input_schema", None) or (t.get("input_schema") if isinstance(t, dict) else None)
            if isinstance(schema, dict):
                return set(schema.get("required", []))
    return set()


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
            # If the raw JSON was truncated and this is a large string value,
            # json_repair may have closed quotes prematurely — the value is garbage
            raw_value_start = f'"{key}"'
            if raw_value_start in raw_json:
                # Check if the value's closing quote is from repair (not original)
                key_pos = raw_json.index(raw_value_start)
                after_key = raw_json[key_pos:]
                # If the raw JSON doesn't have the full value, it was truncated
                if value not in after_key and len(value) > 500:
                    print(f"[no-tools] Deterministic repair for '{tool_name}': field '{key}' appears truncated ({len(value)} chars), rejecting")
                    return None

    print(f"[no-tools] Deterministic recovery OK for '{tool_name}': keys={list(parsed.keys())}")
    return [{
        "type": "tool_use",
        "id": f"toolu_{uuid.uuid4().hex[:24]}",
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
            t_name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
            if t_name == tool_name:
                raw = t.model_dump() if hasattr(t, "model_dump") else (t.dict() if hasattr(t, "dict") else dict(t))
                tool_def = json.dumps(raw, ensure_ascii=False)[:500]
                break

    prompt = (
        "Complete this truncated XML tool call. "
        "Respond ONLY with the complete <tool_call> XML, nothing else.\n\n"
        f"Partial XML:\n{partial_xml[:2000]}\n\n"
    )
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
        tool_blocks, _ = extract_tool_calls_from_text(content)
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
# 6. Streaming XML buffer (state machine)
# ---------------------------------------------------------------------------

_TOOL_CALL_OPEN = "<tool_call"


class XmlToolBuffer:
    """
    State machine for detecting <tool_call> XML tags in a streaming text.
    Feed text chunks, get back ordered segments of text and tool_calls.
    """

    def __init__(self):
        self.buffer: str = ""
        self.in_tool: bool = False

    def feed(self, text: str) -> list[dict]:
        """
        Feed a new text chunk.

        Returns list of segments in order:
            [{"type": "text", "text": "..."}, {"type": "tool_call", "name": ..., "input": {...}}, ...]
        """
        self.buffer += text
        return self._drain()

    def flush(self) -> list[dict]:
        """Flush remaining buffer as text (call at stream end)."""
        if not self.buffer:
            return []
        if "<tool_call" in self.buffer:
            print(f"[no-tools] WARNING: flushing incomplete tool call ({len(self.buffer)} chars). First 300: {self.buffer[:300]}")
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
        """Try to extract text before a <tool_call> tag, or return None if need more data."""
        search_start = 0
        while True:
            idx = self.buffer.find(_TOOL_CALL_OPEN, search_start)
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
            if next_char not in (' ', '\t', '\n', '\r', '>'):
                # Not valid XML tag — regex pattern or other false positive
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

    def _try_extract_tool(self) -> dict | None:
        """Try to extract a complete </tool_call> block, or return None if incomplete."""
        end_tag = "</tool_call>"
        end_idx = self.buffer.find(end_tag)
        if end_idx == -1:
            # Safety: if buffer grows too large without closing tag, it's a false positive
            if len(self.buffer) > self._MAX_TOOL_BUFFER:
                print(f"[xml-buffer] Buffer overflow ({len(self.buffer)} chars > {self._MAX_TOOL_BUFFER}) — false positive <tool_call>, emitting as text")
                text = self.buffer
                self.buffer = ""
                self.in_tool = False
                return {"type": "text", "text": text}
            return None
        end_idx += len(end_tag)
        tool_xml = self.buffer[:end_idx]
        self.buffer = self.buffer[end_idx:]
        self.in_tool = False
        return self._parse_tool_xml(tool_xml)

    def _is_backtick_quoted(self, idx: int) -> bool:
        """Check if <tool_call at position idx is inside backtick-quoted text."""
        # Check for backtick immediately before
        if idx > 0 and self.buffer[idx - 1] == '`':
            return True
        # Check for triple-backtick code block in nearby prefix
        prefix = self.buffer[max(0, idx - 10):idx]
        if '```' in prefix:
            return True
        return False

    def _parse_tool_xml(self, xml: str) -> dict:
        """Parse a complete <tool_call> XML string. Falls back to text on parse failure."""
        print(f"[xml-buffer] _parse_tool_xml: {len(xml)} chars. First 200: {xml[:200]}", flush=True)

        # 1) Primary: known inner tags (<input>, <textarea>, etc.)
        match = _TOOL_CALL_RE.search(xml)
        if match:
            name = match.group(1).strip()
            raw_input = match.group(2)
            parsed = _safe_parse_tool_input(raw_input, name)
            print(f"[xml-buffer] PRIMARY match: name={name} keys={list(parsed.keys())}")
            return {"type": "tool_call", "name": name, "input": parsed}

        # 2) Fallback: any matched pair of XML tags
        fallback_match = _TOOL_CALL_FALLBACK_RE.search(xml)
        if fallback_match:
            name = fallback_match.group(1).strip()
            inner_tag = fallback_match.group(2)
            raw_input = fallback_match.group(3)
            print(f"[xml-buffer] FALLBACK match ({inner_tag}): name={name}")
            parsed = _safe_parse_tool_input(raw_input, name)
            return {"type": "tool_call", "name": name, "input": parsed}

        # 3) Bare: no inner tags, JSON directly inside <tool_call>
        # Also handles tool calls with NO input (e.g. EnterPlanMode, ExitPlanMode)
        bare_match = _TOOL_CALL_BARE_RE.search(xml)
        if bare_match:
            name = bare_match.group(1).strip()
            raw_content = bare_match.group(2).strip()
            print(f"[xml-buffer] BARE match (no inner tags): name={name} content={len(raw_content)} chars")
            parsed = _safe_parse_tool_input(raw_content, name)
            return {"type": "tool_call", "name": name, "input": parsed}

        print(f"[xml-buffer] FAILED all regexes ({len(xml)} chars). Full XML: {xml[:500]}", flush=True)
        return {"type": "text", "text": xml}

    def _safe_text_end(self) -> int:
        """Find safe end position, avoiding partial '<tool_call' matches at buffer end."""
        for i in range(1, min(len(_TOOL_CALL_OPEN), len(self.buffer)) + 1):
            if _TOOL_CALL_OPEN.startswith(self.buffer[-i:]):
                return len(self.buffer) - i
        return len(self.buffer)
