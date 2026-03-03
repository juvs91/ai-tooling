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
from functools import lru_cache
from typing import Any, FrozenSet

from json_repair import repair_json
from utils.utils import get_tool_name, make_tool_id, to_dict


# ---------------------------------------------------------------------------
# 0. Tool name normalization + validation helpers
# ---------------------------------------------------------------------------

# Normalize legacy/model-generated tool names to current Claude Code names.
# Models trained on older CC versions may emit "Task" instead of "Agent".
_TOOL_NAME_ALIASES: dict[str, str] = {
    "Task": "Agent",
}


def _normalize_tool_name(name: str) -> str:
    """Normalize legacy tool names to current Claude Code names (deterministic fallback)."""
    normalized = _TOOL_NAME_ALIASES.get(name, name)
    if normalized != name:
        print(f"[no-tools] Normalized tool name: '{name}' → '{normalized}'")
    return normalized

def _build_valid_tool_names(tools: list | None) -> set[str]:
    """Extract set of valid tool names from request tools."""
    if not tools:
        return set()
    names = set()
    for t in tools:
        name = get_tool_name(t)
        if name:
            names.add(name)
    return names


def validate_tool_name(name: str, valid_names: set[str]) -> bool:
    """Check if tool name is in allowlist. Returns True when no allowlist (backward compat)."""
    if not valid_names:
        return True
    if not name or not isinstance(name, str):
        return False
    return name.strip() in valid_names


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

def _format_schema_properties(input_schema: dict, depth: int = 0, max_depth: int = 2) -> str:
    """Format JSON Schema properties into readable parameter list with recursion into arrays/objects."""
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
        # Show enum values so model knows valid choices
        enum_vals = prop.get("enum")
        if enum_vals and isinstance(enum_vals, list):
            line += f" [values: {', '.join(str(v) for v in enum_vals)}]"
        lines.append(line)
        # Recurse into array items (critical for TodoWrite-like schemas)
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
        # Recurse into nested objects
        elif ptype == "object" and depth < max_depth and prop.get("properties"):
            lines.append(_format_schema_properties(prop, depth=depth + 1, max_depth=max_depth))
    return "\n".join(lines)


def _build_tool_quick_reference(tools: list[dict]) -> str:
    """Build compact reference with nested structure: TodoWrite(todos=[{content, status(pending/in_progress/completed), activeForm}])"""
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
            # Show array item structure (critical for complex tools like TodoWrite)
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
        if params:
            lines.append(f"- {name}({', '.join(params)})")
        else:
            lines.append(f"- {name}()")
    return "\n".join(lines)


# Pre-defined few-shot examples for CC core tools (parameters as JSON, not XML tags)
_FEW_SHOT_EXAMPLES: dict[str, dict] = {
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


def _build_few_shot_examples(tools: list[dict]) -> str:
    """Build few-shot examples for CC core tools present in the request.

    Only includes examples for tools that are actually in the request.
    Reuses _build_valid_tool_names() to filter dynamically.
    """
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
        # Fallback: generic example if no core tools matched
        lines.append('<tool_call name="ToolName">')
        lines.append("<input>")
        lines.append('{"param1": "value1", "param2": "value2"}')
        lines.append("</input>")
        lines.append("</tool_call>\n")

    # Negative examples to prevent common hallucinations
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

    # Extract tool names for explicit allowlist
    tool_names = sorted(_build_valid_tool_names(tools))
    if tool_names:
        header += (
            "VALID TOOL NAMES (use ONLY these, NEVER invent others):\n"
            + ", ".join(tool_names) + "\n"
            "If a tool you need is not in this list, explain what you need — do NOT fabricate a tool name.\n\n"
        )

    # Few-shot examples from actual tools in the request
    header += _build_few_shot_examples(tools)

    # Compact reference of ALL available tools
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
# Skip optional <reasoning>...</reasoning> tags that models may inject inside <tool_call>
_REASONING_SKIP = r'(?:<reasoning>[\s\S]*?</reasoning>\s*)*'
_TOOL_CALL_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*{_REASONING_SKIP}<{_INNER_TAG}>([\s\S]*?)</{_INNER_TAG}>\s*</tool_call>',
    re.DOTALL,
)
# Greedy variant: matches the LAST </input></tool_call> instead of first.
# Used when JSON content contains nested <tool_call>/<input> XML examples.
_TOOL_CALL_GREEDY_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*{_REASONING_SKIP}<{_INNER_TAG}>([\s\S]*)</{_INNER_TAG}>\s*</tool_call>',
    re.DOTALL,
)
# Fallback regex: matches any single XML tag wrapping the content
_TOOL_CALL_FALLBACK_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*{_REASONING_SKIP}<(\w+)>([\s\S]*?)</\2>\s*</tool_call>',
    re.DOTALL,
)
# Last-resort regex: NO inner tags — JSON directly inside <tool_call>
# Handles: <tool_call name="Read">{"file_path": "/path"}</tool_call>
_TOOL_CALL_BARE_RE = re.compile(
    rf'<tool_call\s+{_NAME_ATTR}\s*>\s*([\s\S]*?)\s*</tool_call>',
    re.DOTALL,
)


# GLM format: <tool_call>ToolName<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>
_TOOL_CALL_ARGKV_RE = re.compile(
    r'<tool_call>([\w]+)((?:\s*<arg_key>[\s\S]*?</arg_key>\s*<arg_value>[\s\S]*?</arg_value>)+)\s*</tool_call>',
    re.DOTALL,
)
_ARG_KV_PAIR_RE = re.compile(
    r'<arg_key>([\s\S]*?)</arg_key>\s*<arg_value>([\s\S]*?)</arg_value>',
    re.DOTALL,
)

# 5th fallback: diluted XML format after prompt dilution — models invent their own tags
# Handles: <tool_name>Read</tool_name><args>{"file_path": "..."}</args>
# or:      <tool_name>Read</tool_name><arguments>{"file_path": "..."}</arguments>
_TOOL_DILUTED_RE = re.compile(
    r'<tool_name>([\w]+)</tool_name>\s*<(?:args|arguments|params|input)>([\s\S]*?)</(?:args|arguments|params|input)>',
    re.DOTALL,
)


def _parse_argkv_tool(match) -> dict:
    """Parse a <tool_call>Name<arg_key>k</arg_key><arg_value>v</arg_value></tool_call> match."""
    name = match.group(1).strip()
    args_xml = match.group(2)
    input_dict = {}
    for kv in _ARG_KV_PAIR_RE.finditer(args_xml):
        input_dict[kv.group(1).strip()] = kv.group(2)
    return {"name": name, "input": input_dict}


# 6th fallback: XML-as-tags format — model uses XML param tags instead of JSON
# Handles: <file_path>/path</file_path> <content>text</content>
_XML_PARAM_TAG_RE = re.compile(r'<(\w+)>([\s\S]*?)</\1>', re.DOTALL)

# 7th fallback: Attributed XML parameter format (Anthropic SDK style)
# Handles: <parameter name="file_path">/path</parameter>
_XML_ATTR_PARAM_RE = re.compile(
    r'<parameter\s+name=["\'](\w+)["\']\s*>([\s\S]*?)</parameter>',
    re.DOTALL,
)


_CDATA_RE = re.compile(r'^<!\[CDATA\[([\s\S]*?)\]\]>$')


def _strip_inner_xml_tags(raw: str) -> str:
    """
    Strip wrapping XML inner tags if present.
    Handles: <input>{json}</input> → {json}
             <textarea>{json}</textarea> → {json}
             <![CDATA[{json}]]> → {json}
             <input><![CDATA[{json}]]></input> → {json}
             <reasoning>...</reasoning>{json} → {json}
             {json} → {json} (no-op)
    """
    stripped = raw.strip()
    # Strip <reasoning> tags that models may inject before/around the JSON
    stripped = re.sub(r'<reasoning>[\s\S]*?</reasoning>', '', stripped).strip()
    # Strip CDATA wrapper if present (XML standard construct)
    cdata_match = _CDATA_RE.match(stripped)
    if cdata_match:
        stripped = cdata_match.group(1).strip()
    # Try to remove a matched pair of XML tags wrapping the content
    tag_match = re.match(r'^<(\w+)>([\s\S]*)</\1>$', stripped, re.DOTALL)
    if tag_match:
        stripped = tag_match.group(2).strip()
    # Strip CDATA again (may have been inside the XML tag)
    cdata_match = _CDATA_RE.match(stripped)
    if cdata_match:
        stripped = cdata_match.group(1).strip()
    return stripped


def _parse_xml_as_tags(raw: str, tool_name: str, tools: list | None = None) -> dict | None:
    """
    Convert XML-as-tags format to JSON dict.

    Some models (DeepSeek-reasoner) generate parameters as XML tags instead of JSON:
        Format A: <file_path>/path/to/file</file_path><content>text</content>
        Format B: <parameter name="file_path">/path</parameter><parameter name="content">text</parameter>
    instead of:
        {"file_path": "/path/to/file", "content": "text"}

    Returns dict if XML-as-tags detected and validated against schema, None otherwise.
    """
    stripped = raw.strip()
    # Quick check: must start with < and contain at least one <tag>...</tag> pair
    if not stripped.startswith("<") or "</" not in stripped:
        return None

    # Try Format A: <param_name>value</param_name>
    matches = list(_XML_PARAM_TAG_RE.finditer(stripped))
    use_attr_format = False
    if not matches:
        # Try Format B: <parameter name="param_name">value</parameter>
        matches = list(_XML_ATTR_PARAM_RE.finditer(stripped))
        use_attr_format = True
    if not matches:
        return None

    result = {}
    for m in matches:
        param_name = m.group(1)
        if not use_attr_format:
            # Format A: skip known non-param tags (reasoning, input, tool_call)
            if param_name in ("reasoning", "input", "tool_call", "textarea", "arguments", "params"):
                return None
        result[param_name] = m.group(2)

    if not result:
        return None

    # Schema validation: extracted keys must overlap with tool schema properties
    props = _get_tool_properties(tool_name, tools)
    if props:
        overlap = set(result.keys()) & set(props.keys())
        if not overlap:
            return None  # No schema overlap — not XML-as-tags for this tool

    fmt = "xml-attr" if use_attr_format else "xml-as-tags"
    print(f"[no-tools] Parsed {fmt} format for tool '{tool_name}': keys={list(result.keys())}")
    return result


def _greedy_extract_json_fields(raw: str, tool_name: str, tools: list | None) -> dict | None:
    """
    Greedy field extraction for tools with large string content (Write, Edit).

    When json.loads and repair_json fail (e.g. DeepSeek emits unescaped quotes
    inside the 'content' field), extract fields using regex that tolerates broken JSON.

    Strategy: extract each field value by finding the key, then greedily capturing
    the value up to the next key or end of JSON.
    """
    if not tools:
        return None
    props = _get_tool_properties(tool_name, tools)
    required = _get_tool_required_fields(tool_name, tools)
    if not props or len(required) < 2:
        return None  # Only needed for multi-field tools like Write/Edit

    result = {}
    prop_names = list(props.keys())

    for i, field in enumerate(prop_names):
        expected_type = props[field].get("type") if isinstance(props[field], dict) else None
        # Build regex: "field_name"\s*:\s*"(value)"
        # For the LAST string field, capture greedily to the end of the JSON
        if expected_type == "string":
            # Find all potential next-field boundaries
            next_fields = prop_names[i + 1:]
            if next_fields:
                # Non-greedy: capture until the next field key
                boundary = "|".join(re.escape(f'"{nf}"') for nf in next_fields)
                pattern = rf'"{re.escape(field)}"\s*:\s*"([\s\S]*?)"\s*,\s*(?:{boundary})'
                m = re.search(pattern, raw)
                if not m:
                    # Greedy fallback: capture to end of JSON
                    pattern = rf'"{re.escape(field)}"\s*:\s*"([\s\S]+?)"\s*[,}}]\s*$'
                    m = re.search(pattern, raw)
            else:
                # Last field: capture greedily to the closing brace
                pattern = rf'"{re.escape(field)}"\s*:\s*"([\s\S]+?)"\s*\}}\s*$'
                m = re.search(pattern, raw)
            if m:
                val = m.group(1)
                # Unescape JSON sequences
                val = val.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
                result[field] = val
        elif expected_type == "boolean":
            m = re.search(rf'"{re.escape(field)}"\s*:\s*(true|false)', raw, re.IGNORECASE)
            if m:
                result[field] = m.group(1).lower() == "true"

    # Validate: all required fields must be present
    missing = required - set(result.keys())
    if missing:
        return None

    print(f"[no-tools] Greedy field extraction OK for '{tool_name}': keys={list(result.keys())}")
    return result


def _schema_aware_cleanup(parsed: dict, tool_name: str, tools: list | None) -> dict:
    """
    Filter parsed dict to only include keys from the tool's schema.

    When repair_json produces extra keys from misinterpreted string boundaries,
    keep only schema-valid keys. Returns original dict if no schema available
    or if cleanup would remove required fields.
    """
    if not tools:
        return parsed
    props = _get_tool_properties(tool_name, tools)
    if not props:
        return parsed

    extra_keys = set(parsed.keys()) - set(props.keys())
    if not extra_keys:
        return parsed  # All keys are valid

    required = _get_tool_required_fields(tool_name, tools)
    cleaned = {k: v for k, v in parsed.items() if k in props}
    missing = required - set(cleaned.keys())
    if missing:
        return parsed  # Cleanup would lose required fields — keep original

    print(f"[no-tools] Schema cleanup for '{tool_name}': removed extra keys {extra_keys}")
    return cleaned


def _safe_parse_tool_input(raw_input: str, tool_name: str, tools: list | None = None) -> dict:
    """
    Parse tool input JSON with multiple fallback strategies.
    NEVER raises — always returns a valid dict.

    When parsed value is not a dict (e.g. array), wraps as {"value": parsed}
    then attempts to repair using _repair_tool_input if tools schema is available.
    """
    raw = raw_input.strip()
    if not raw:
        return {}

    # 0) Strip any wrapping XML tags (e.g. <input>...</input>)
    raw = _strip_inner_xml_tags(raw)

    # 0.5) Detect XML-as-tags format: <param>value</param> instead of JSON
    xml_tags_result = _parse_xml_as_tags(raw, tool_name, tools=tools)
    if xml_tags_result is not None:
        return xml_tags_result

    def _wrap_and_repair(parsed: Any) -> dict:
        """Wrap non-dict parsed value and attempt schema-based repair."""
        wrapped = {"value": parsed}
        if tools:
            return _repair_tool_input(tool_name, wrapped, tools)
        return wrapped

    # 1) Direct JSON parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return _schema_aware_cleanup(parsed, tool_name, tools)
        return _wrap_and_repair(parsed)
    except json.JSONDecodeError:
        pass

    # 2) json_repair
    try:
        repaired = repair_json(raw, return_objects=True)
        if isinstance(repaired, dict):
            print(f"[no-tools] Repaired malformed JSON for tool '{tool_name}'")
            return _schema_aware_cleanup(repaired, tool_name, tools)
        return _wrap_and_repair(repaired)
    except Exception:
        pass

    # 3) Greedy field extraction (for Write/Edit with unescaped quotes in content)
    greedy = _greedy_extract_json_fields(raw, tool_name, tools)
    if greedy is not None:
        return greedy

    # 4) Last resort: wrap raw string
    print(f"[no-tools] Could not parse tool input for '{tool_name}', wrapping as raw")
    return {"raw_input": raw}


def extract_tool_calls_from_text(text: str, valid_tool_names: set[str] | None = None, tools: list | None = None) -> tuple[list[dict], str]:
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

        # 5th fallback: diluted XML format (models invent <tool_name>/<args> after prompt dilution)
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


def _type_compatible(value: Any, schema_type: str) -> bool:
    """Check if a Python value is compatible with a JSON Schema type."""
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    expected = type_map.get(schema_type)
    if expected is None:
        return True  # Unknown schema type — don't block
    return isinstance(value, expected)


def _repair_tool_input(name: str, input_dict: dict, tools: list | None) -> dict:
    """
    Repair tool input by rewrapping {"value": ...} to the correct field name
    based on the tool's schema.

    When _safe_parse_tool_input encounters a non-dict value (e.g. array for TodoWrite),
    it wraps as {"value": parsed}. This function uses the schema to find the correct
    field name and rewraps accordingly.
    """
    if not tools or "value" not in input_dict or len(input_dict) != 1:
        return input_dict

    schema = _get_tool_schema(name, tools)
    if not schema:
        return input_dict

    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    value = input_dict["value"]

    # Strategy 1: Single required array property + value is a list
    if isinstance(value, list):
        array_props = [k for k, v in props.items() if isinstance(v, dict) and v.get("type") == "array"]
        required_array = [k for k in array_props if k in required]
        if len(required_array) == 1:
            field = required_array[0]
            print(f"[repair] Rewrapped {{'value': [...]}} → {{'{field}': [...]}} for tool '{name}'")
            return {field: value}
        # Strategy 1b: List with single string element → unwrap for string fields
        if len(value) == 1 and isinstance(value[0], str) and len(required) == 1:
            field = list(required)[0]
            expected_type = props.get(field, {}).get("type") if isinstance(props.get(field), dict) else None
            if expected_type == "string":
                print(f"[repair] Unwrapped single-element list → string for '{field}' in tool '{name}'")
                return {field: value[0]}

    # Strategy 2: Single required property (type-checked)
    if len(required) == 1:
        field = list(required)[0]
        if field in props:
            expected_type = props[field].get("type") if isinstance(props[field], dict) else None
            if expected_type and not _type_compatible(value, expected_type):
                print(f"[repair] SKIP rewrap: value type {type(value).__name__} "
                      f"incompatible with schema type '{expected_type}' for '{field}' in tool '{name}'")
                return input_dict
            print(f"[repair] Rewrapped {{'value': ...}} → {{'{field}': ...}} for tool '{name}'")
            return {field: value}

    # Strategy 3: value is a dict with keys matching schema (multi-field tools like Write/Edit)
    # Handles: {"value": {"file_path": "...", "content": "..."}} → {"file_path": "...", "content": "..."}
    if isinstance(value, dict):
        overlap = set(value.keys()) & set(props.keys())
        missing = required - set(value.keys())
        if overlap and not missing:
            print(f"[repair] Unwrapped {{'value': dict}} for tool '{name}': keys={list(value.keys())}")
            return value

    return input_dict


# ---------------------------------------------------------------------------
# 5. Recovery for truncated tool calls
# ---------------------------------------------------------------------------

# Regex to extract partial tool call: name + whatever JSON we got
_PARTIAL_TOOL_RE = re.compile(
    r'<tool_call\s+' + _NAME_ATTR + r'\s*>\s*' + _REASONING_SKIP + r'<' + _INNER_TAG + r'>\s*([\s\S]*)',
    re.DOTALL,
)

# Partial argkv: <tool_call>Name<arg_key>k</arg_key><arg_value>v</arg_value>...(truncated)
_PARTIAL_ARGKV_RE = re.compile(
    r'<tool_call>(\w+)((?:\s*<arg_key>[\s\S]*?</arg_key>\s*<arg_value>[\s\S]*?</arg_value>)*[\s\S]*)',
    re.DOTALL,
)

# Partial XML-as-tags: <tool_call name="Write"><file_path>...</file_path><content>...(truncated)
_PARTIAL_XML_TAGS_RE = re.compile(
    r'<tool_call\s+' + _NAME_ATTR + r'\s*>\s*((?:<\w+>[\s\S]*?</\w+>\s*)*)',
    re.DOTALL,
)


def _get_tool_schema(tool_name: str, tools: list | None) -> dict | None:
    """Get a tool's input_schema by name. Returns None if not found."""
    if not tools:
        return None
    for t in tools:
        if get_tool_name(t) == tool_name:
            schema = getattr(t, "input_schema", None) or (t.get("input_schema") if isinstance(t, dict) else None)
            if isinstance(schema, dict):
                return schema
    return None


def _get_tool_required_fields(tool_name: str, tools: list | None) -> set[str]:
    """Get required fields from a tool's input_schema."""
    schema = _get_tool_schema(tool_name, tools)
    if not schema:
        return set()
    return set(schema.get("required", []))


def _get_tool_properties(tool_name: str, tools: list | None) -> dict:
    """Get properties dict from a tool's input_schema."""
    schema = _get_tool_schema(tool_name, tools)
    if not schema:
        return {}
    return schema.get("properties", {})


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
    # Remove incomplete <tool_call...> fragments (no closing tag)
    # Bounded match (8000/2000 chars) prevents destroying all content after a false positive
    cleaned = re.sub(r'<tool_call\s+[^>]*>(?:(?!</tool_call>)[\s\S]){0,8000}$', '', cleaned)
    cleaned = re.sub(r'<tool_call>[A-Za-z]\w*(?:<arg_key>[\s\S]*?</arg_key>\s*<arg_value>[\s\S]*?</arg_value>)*(?:(?!</tool_call>)[\s\S]){0,2000}$', '', cleaned)
    # Orphaned opening AND closing inner tags
    cleaned = re.sub(r'</?(?:tool_call|input|textarea|arguments|params|arg_key|arg_value)>', '', cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# 6. Streaming XML buffer (state machine)
# ---------------------------------------------------------------------------

_TOOL_CALL_OPEN = "<tool_call"

# Detects if opening <tool_call has REAL (unescaped) quotes in name= attribute
# Used in _try_extract_tool to distinguish real tools from escaped examples
_REAL_NAME_RE = re.compile(r'<tool_call\s+name=["\'][^"\']+["\']')


def _normalize_escaped_xml(xml: str) -> str | None:
    """Unescape JSON-encoded XML that leaked from content strings.

    Handles: name=\\"Read\\" → name="Read", \\n → newline, \\t → tab.
    Returns None if no escaping detected (no work needed).
    """
    if '\\"' not in xml and '\\n' not in xml:
        return None
    result = xml
    # Handle double-escaped first (\\\\\" → \\\", then \\" → ")
    while '\\\\"' in result:
        result = result.replace('\\\\"', '\\"')
    while '\\\\n' in result:
        result = result.replace('\\\\n', '\\n')
    result = result.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
    return result if result != xml else None


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
        if first == '>':
            rest = after[1:]
            if rest and rest[0].isalpha():
                return True
            return False  # <tool_call>` or <tool_call>\n — not a tool
        return False  # <tool_call(, <tool_call? — not a tool

    def flush(self) -> list[dict]:
        """Flush remaining buffer at stream end.

        Returns: "text", "tool_call", or "incomplete_tool_call" segments.
        """
        if not self.buffer:
            return []
        if "<tool_call" in self.buffer and self._has_plausible_tool_call():
            print(f"[no-tools] WARNING: flushing incomplete tool call "
                  f"({len(self.buffer)} chars). First 300: {self.buffer[:300]}")
            segments = []
            idx = self.buffer.find("<tool_call")
            if idx > 0:
                prefix = self.buffer[:idx].strip()
                if prefix:
                    segments.append({"type": "text", "text": prefix})
            incomplete = self.buffer[idx:] if idx >= 0 else self.buffer
            segments.append({"type": "incomplete_tool_call", "text": incomplete})
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
                if not self.buffer[name_start].isalpha():
                    # Not a valid tool name start — skip this occurrence
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

            # More </tool_call> tags in buffer → the one we found might be inside JSON content
            if end_tag in self.buffer[candidate_end:]:
                search_start = candidate_end
                continue

            # Last </tool_call> — determine if this is the real closing tag
            has_real_name = bool(_REAL_NAME_RE.search(self.buffer[:60]))

            if has_real_name:
                # Real tool call (unescaped quotes in name=) — check for premature extraction
                # If <input> is present but unclosed (no </input>), outer structure
                # hasn't arrived yet. If both <input> AND </input> are present,
                # the structure is complete but PRIMARY failed for another reason
                # (e.g. <system-reminder> prefix tag) — proceed to parse.
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
                # No inner XML tags → genuine BARE format
                self.buffer = self.buffer[candidate_end:]
                self.in_tool = False
                return self._parse_tool_xml(tool_xml)

            # Escaped name= (backslash-quotes from JSON content) → parse with normalization
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
                # Triggers when: embedded <tool_call, or multiple </input> or </tool_call> tags
                # (common when Write content describes XML tool format)
                _has_embedded_xml = (
                    _TOOL_CALL_OPEN in raw_input
                    or attempt_xml.count('</input>') > 1
                    or attempt_xml.count('</tool_call>') > 1
                )
                if _has_embedded_xml:
                    # Bound greedy search to first tool only (avoid consuming subsequent tools)
                    real_opens = [m.start() for m in _REAL_NAME_RE.finditer(attempt_xml)]
                    if len(real_opens) > 1:
                        # Multiple real tools — restrict greedy to first tool's boundary
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
        """Find safe end position, avoiding partial '<tool_call' matches at buffer end."""
        for i in range(1, min(len(_TOOL_CALL_OPEN), len(self.buffer)) + 1):
            if _TOOL_CALL_OPEN.startswith(self.buffer[-i:]):
                return len(self.buffer) - i
        return len(self.buffer)
