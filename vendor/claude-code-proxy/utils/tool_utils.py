"""Tool utilities migrated from tool_prompting.py

These utilities provide tool name validation, schema access, and model detection.
They are shared across multiple transformers and should be in a separate module.
"""

import os
from functools import lru_cache
from typing import Any, List, Dict, FrozenSet
from utils.utils import get_tool_name


# ---------------------------------------------------------------------------
# Model detection
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


def normalize_tool_name(name: str) -> str:
    """Normalize legacy tool names (Task → Agent)."""
    legacy_to_agent = {
        "Task": "Agent",
        "Agent": "Agent",
        # Add more mappings if needed
    }
    return legacy_to_agent.get(name, name)


def build_valid_tool_names(tools: List[Dict]) -> set[str]:
    """Extract valid tool names from tool definitions."""
    if not tools:
        return set()

    valid_names = set()
    for tool in tools:
        name = get_tool_name(tool)
        if name:
            valid_names.add(name)
    return valid_names


def validate_tool_name(name: str, valid_names: set[str]) -> bool:
    """Check if tool name is in allowlist. Returns True when no allowlist (backward compat)."""
    if not valid_names:
        return True
    if not name or not isinstance(name, str):
        return False
    return name.strip() in valid_names


def get_tool_schema(tool_name: str, tools: List[Dict]) -> Dict | None:
    """Get input schema for a tool by name."""
    if not tools:
        return None

    for tool in tools:
        if tool.get("name") == tool_name:
            return tool.get("input_schema")
    return None


def get_tool_required_fields(tool_name: str, tools: List[Dict]) -> List[str]:
    """Get required fields for a tool by name."""
    if not tools:
        return []

    for tool in tools:
        if tool.get("name") == tool_name:
            schema = tool.get("input_schema", {})
            required = schema.get("required", [])
            return required
    return []


def get_tool_properties(tool_name: str, tools: List[Dict]) -> Dict:
    """Get properties dict for a tool by name."""
    if not tools:
        return {}

    for tool in tools:
        if tool.get("name") == tool_name:
            schema = tool.get("input_schema", {})
            return schema.get("properties", {})
    return {}
