"""
Tool Extraction Helper Functions

Helper functions for parsing and validating tool input from various model outputs.
These functions are used across multiple transformers and converters.
"""
import json
import re
from json_repair import repair_json
from utils.utils import get_tool_name


def _strip_inner_xml_tags(raw: str) -> str:
    """Strip wrapping XML inner tags if present.

    Handles: <input>{json}</input> -> {json}
             <textarea>{json}</textarea> -> {json}
             <![CDATA[{json}]]> -> {json}
             <input><![CDATA[{json}]]></input> -> {json}
             <reasoning>...</reasoning>{json} -> {json}
             {json} -> {json} (no-op)
    """
    from utils.tool_extraction_patterns import _CDATA_RE

    stripped = raw.strip()
    # Strip <reasoning> tags that models may inject before/around the JSON
    stripped = re.sub(r'<reasoning>[\s\S]*?</reasoning>', '', stripped).strip()
    # Strip CDATA wrapper if present (XML standard construct)
    cdata_match = _CDATA_RE.match(stripped)
    if cdata_match:
        stripped = cdata_match.group(1).strip()
    # Try to remove a single matched pair of XML tags wrapping the entire content
    tag_match = re.match(r'^<(\w+)>([\s\S]*)</\1>$', stripped, re.DOTALL)
    if tag_match:
        stripped = tag_match.group(2).strip()
    # Strip CDATA again (may have been inside the XML tag)
    cdata_match = _CDATA_RE.match(stripped)
    if cdata_match:
        stripped = cdata_match.group(1).strip()
    return stripped


def _normalize_escaped_xml(xml: str) -> str | None:
    """Unescape JSON-encoded XML that leaked from content strings.

    Handles: name=\\"Read\\" -> name="Read", \\n -> newline, \\t -> tab.
    Returns None if no escaping detected (no work needed).
    """
    if '\\"' not in xml and '\\n' not in xml:
        return None
    result = xml
    # Handle double-escaped first (\\\\\" -> \\\", then \\" -> ")
    while '\\\\"' in result:
        result = result.replace('\\\\"', '\\"')
    while '\\\\n' in result:
        result = result.replace('\\\\n', '\\n')
    result = result.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
    return result if result != xml else None


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
    from utils.tool_extraction_patterns import _XML_PARAM_TAG_RE, _XML_ATTR_PARAM_RE

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
            return None  # No schema overlap - not XML-as-tags for this tool

    fmt = "xml-attr" if use_attr_format else "xml-as-tags"
    print(f"[no-tools] Parsed {fmt} format for tool '{tool_name}': keys={list(result.keys())}")
    return result


def _parse_argkv_tool(match) -> dict:
    """Parse a  <tool_callName arg_key>value</arg_value> </think> match."""
    name = match.group(1).strip()
    from utils.tool_extraction_patterns import _ARG_KV_PAIR_RE
    args_xml = match.group(2)
    input_dict = {}
    for kv in _ARG_KV_PAIR_RE.finditer(args_xml):
        input_dict[kv.group(1).strip()] = kv.group(2)
    return {"name": name, "input": input_dict}


def _type_compatible(value: any, schema_type: str) -> bool:
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
        return True  # Unknown schema type - don't block
    return isinstance(value, expected)


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
                # Last field: capture greedily to closing brace
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
        return parsed  # Cleanup would lose required fields - keep original

    print(f"[no-tools] Schema cleanup for '{tool_name}': removed extra keys {extra_keys}")
    return cleaned


def _safe_parse_tool_input(raw_input: str, tool_name: str, tools: list | None = None) -> dict:
    """
    Parse tool input JSON with multiple fallback strategies.
    NEVER raises - always returns a valid dict.

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

    def _wrap_and_repair(parsed: any) -> dict:
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
            print(f"[repair] Rewrapped {{'value': [...]}} -> {{'{field}': [...]}} for tool '{name}'")
            return {field: value}
        # Strategy 1b: List with single string element -> unwrap for string fields
        if len(value) == 1 and isinstance(value[0], str) and len(required) == 1:
            field = list(required)[0]
            expected_type = props.get(field, {}).get("type") if isinstance(props.get(field), dict) else None
            if expected_type == "string":
                print(f"[repair] Unwrapped single-element list -> string for '{field}' in tool '{name}'")
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
            print(f"[repair] Rewrapped {{'value': ...}} -> {{'{field}': ...}} for tool '{name}'")
            return {field: value}

    # Strategy 3: value is a dict with keys matching schema (multi-field tools like Write/Edit)
    # Handles: {"value": {"file_path": "...", "content": "..."}} -> {"file_path": "...", "content": "..."}
    if isinstance(value, dict):
        overlap = set(value.keys()) & set(props.keys())
        missing = required - set(value.keys())
        if overlap and not missing:
            print(f"[repair] Unwrapped {{'value': dict}} for tool '{name}': keys={list(value.keys())}")
            return value

    return input_dict


# Export all functions
__all__ = [
    '_strip_inner_xml_tags',
    '_normalize_escaped_xml',
    '_parse_xml_as_tags',
    '_parse_argkv_tool',
    '_type_compatible',
    '_get_tool_schema',
    '_get_tool_required_fields',
    '_get_tool_properties',
    '_greedy_extract_json_fields',
    '_schema_aware_cleanup',
    '_safe_parse_tool_input',
    '_repair_tool_input',
]
