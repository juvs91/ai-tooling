# utils/schema_utils.py — Gemini / provider schema sanitization utilities
"""
Schema cleaning and tool definition conversion helpers.

These functions are provider-specific utilities (Gemini / Vertex) for sanitizing
JSON Schema to remove unsupported keywords and converting Anthropic tool dicts
to OpenAI format with memoization.

Migrated from llm/converters.py to keep format-conversion logic separate from
schema sanitization logic.
"""
from __future__ import annotations

import hashlib
import json
from threading import Lock
from typing import Any


# ── Module-level memoization state ───────────────────────────────────────────

_gemini_schema_cache: dict[str, Any] = {}
_tool_conversion_cache: dict[str, dict] = {}
_schema_cache_lock = Lock()  # shared lock for both caches


# ── Gemini schema sanitization ────────────────────────────────────────────────

def clean_gemini_schema(schema: Any) -> Any:
    """
    Sanitizer best-effort para Gemini / Vertex tools schemas.
    Mantiene subset seguro; elimina keywords que suelen romper validación.
    """
    DROP_KEYS = {
        "$schema", "$id", "id", "$ref",
        "definitions", "$defs",
        "additionalProperties", "unevaluatedProperties",
        "propertyNames", "patternProperties",
        "dependencies", "dependentSchemas", "dependentRequired",
        "contentEncoding", "contentMediaType",
        "examples", "example", "default",
        "readOnly", "writeOnly", "deprecated",
        "not", "if", "then", "else",
        "contains", "minContains", "maxContains",
        "const",
        "discriminator",
    }
    ALLOWED_STRING_FORMATS = {"date-time"}

    def _merge_dict(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = _merge_dict(out[k], v)
            else:
                out[k] = v
        return out

    def _normalize_type(d: dict) -> dict:
        t = d.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            if len(non_null) == 1:
                d["type"] = non_null[0]
            elif len(non_null) > 1:
                d["type"] = non_null[0]
            else:
                d.pop("type", None)
        if "type" not in d and "properties" in d and isinstance(d["properties"], dict):
            d["type"] = "object"
        return d

    def _rewrite_const(d: dict) -> dict:
        if "const" in d:
            d["enum"] = [d["const"]]
            d.pop("const", None)
        return d

    def _rewrite_exclusive_bounds(d: dict) -> dict:
        if "exclusiveMinimum" in d and "minimum" not in d:
            d["minimum"] = d["exclusiveMinimum"]
        d.pop("exclusiveMinimum", None)
        if "exclusiveMaximum" in d and "maximum" not in d:
            d["maximum"] = d["exclusiveMaximum"]
        d.pop("exclusiveMaximum", None)
        return d

    def _drop_unsupported_keys(d: dict) -> dict:
        for k in list(d.keys()):
            if k in DROP_KEYS:
                d.pop(k, None)
        return d

    def _clean(x: Any) -> Any:
        if isinstance(x, list):
            return [_clean(i) for i in x]
        if not isinstance(x, dict):
            return x

        x = _rewrite_const(x)
        x = _rewrite_exclusive_bounds(x)
        x = _normalize_type(x)

        if x.get("type") == "string" and "format" in x:
            if x["format"] not in ALLOWED_STRING_FORMATS:
                x.pop("format", None)

        x = _drop_unsupported_keys(x)

        if "allOf" in x and isinstance(x["allOf"], list) and x["allOf"]:
            base = dict(x)
            parts = base.pop("allOf", [])
            merged = {}
            for p in parts:
                p = _clean(p)
                if isinstance(p, dict):
                    merged = _merge_dict(merged, p)
            x = _merge_dict(base, merged)

        for key in ("anyOf", "oneOf"):
            if key in x and isinstance(x[key], list) and x[key]:
                first = _clean(x[key][0])
                base = dict(x)
                base.pop(key, None)
                if isinstance(first, dict):
                    x = _merge_dict(base, first)
                else:
                    x = base

        if "properties" in x and isinstance(x["properties"], dict):
            for pk, pv in list(x["properties"].items()):
                x["properties"][pk] = _clean(pv)

        if "items" in x:
            x["items"] = _clean(x["items"])

        if "required" in x and not isinstance(x["required"], list):
            x.pop("required", None)
        if "enum" in x and not isinstance(x["enum"], list):
            x.pop("enum", None)

        x.pop("additionalProperties", None)

        x = _normalize_type(x)
        x = _drop_unsupported_keys(x)
        return x

    cleaned = _clean(schema)
    if isinstance(cleaned, dict):
        if cleaned.get("type") is None and "properties" in cleaned:
            cleaned["type"] = "object"
    return cleaned


def clean_gemini_schema_cached(schema: Any) -> Any:
    """Memoized wrapper around clean_gemini_schema."""
    key = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]
    with _schema_cache_lock:
        if key not in _gemini_schema_cache:
            _gemini_schema_cache[key] = clean_gemini_schema(schema)
        return _gemini_schema_cache[key]


# Anthropic server-side tools are resolved by Anthropic's own backend — the
# client/proxy never executes them, and they carry no input_schema. They cannot
# be represented as OpenAI function-calling or an XML tool prompt for a
# non-Anthropic backend, so they're filtered out before conversion. See ADR-0029.
_SERVER_TOOL_TYPE_PREFIXES = (
    "web_search_",
    "web_fetch_",
    "bash_",
    "code_execution_",
    "text_editor_",
)


def is_server_tool(tool_dict: dict) -> bool:
    """True if tool_dict is an Anthropic server-side tool (see ADR-0029)."""
    tool_type = tool_dict.get("type") or ""
    return tool_type.startswith(_SERVER_TOOL_TYPE_PREFIXES)


def _convert_tool_cached(tool_dict: dict, is_gemini: bool) -> dict:
    """Convert Anthropic tool dict to OpenAI format with memoization."""
    name = tool_dict["name"]
    input_schema = tool_dict.get("input_schema", {}) or {}
    schema_str = json.dumps(input_schema, sort_keys=True)
    key = f"{name}:{'g' if is_gemini else 'o'}:{hashlib.sha256(schema_str.encode()).hexdigest()[:16]}"

    with _schema_cache_lock:
        if key in _tool_conversion_cache:
            return _tool_conversion_cache[key]

        if is_gemini:
            input_schema = clean_gemini_schema(input_schema)  # uncached: already inside lock

        converted = {
            "type": "function",
            "function": {
                "name": name,
                "description": tool_dict.get("description", "") or "",
                "parameters": input_schema,
            },
        }
        _tool_conversion_cache[key] = converted
        return converted
