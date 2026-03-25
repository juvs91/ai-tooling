"""Tool utilities migrated from tool_prompting.py

These utilities provide tool name validation, schema access, and model detection.
They are shared across multiple transformers and should be in a separate module.
"""

import os
import re
from functools import lru_cache
from typing import Any, List, Dict, FrozenSet
from utils.utils import get_tool_name


# ---------------------------------------------------------------------------
# Deferred tools (Claude Code injects these in system prompt, not request.tools)
# ---------------------------------------------------------------------------

_DEFERRED_TOOLS_RE = re.compile(
    r'<available-deferred-tools>\s*(.*?)\s*</available-deferred-tools>',
    re.DOTALL,
)

# Fallback: Claude Code ALSO injects deferred tool names via a <system-reminder>
# in user messages using the ToolSearch format (native Claude mechanism).
# This handles cases where the <available-deferred-tools> block is absent from
# the system prompt (e.g. different CC versions or configurations).
_TOOLSEARCH_DEFERRED_RE = re.compile(
    r'The following deferred tools are now available via ToolSearch:\s*\n((?:[ \t]*\w+[ \t]*\n?)+)',
    re.DOTALL,
)

# Known CC workflow tools — used as a safety filter when extracting from messages
# to prevent injecting arbitrary tool names from untrusted content.
_CC_WORKFLOW_TOOL_NAMES: frozenset[str] = frozenset({
    "EnterPlanMode", "ExitPlanMode", "TodoWrite",
    "AskUserQuestion", "CronCreate", "CronDelete", "CronList",
    "EnterWorktree", "ExitWorktree", "TaskOutput", "TaskStop",
    "NotebookEdit", "WebFetch", "WebSearch", "ToolSearch",
})


def _content_to_text(content: Any) -> str:
    """Convert message content (str or list of blocks) to plain text."""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                parts.append(block.text or "")
        return "\n".join(parts)
    return str(content)


def extract_deferred_tool_names(
    system: str | list | None,
    messages: list | None = None,
) -> list[str]:
    """Parse deferred tool names from Claude Code's request.

    Claude Code injects special tools (EnterPlanMode, ExitPlanMode, TodoWrite,
    AskUserQuestion, etc.) in one of two ways:

    1. Primary: <available-deferred-tools> block in the system prompt field.
       Standard format when CC sends requests to a proxy endpoint.

    2. Fallback: <system-reminder>The following deferred tools are now available
       via ToolSearch: ...</system-reminder> in user messages. CC's native
       ToolSearch mechanism — used alongside or instead of (1) in some versions.

    The fallback only activates when (1) finds nothing. Results from messages
    are filtered against known CC workflow tools to prevent injection of
    arbitrary names from untrusted message content.

    Returns a list of tool names (one per non-empty line).
    """
    # ── Primary: system prompt <available-deferred-tools> ───────────────────
    if system:
        if isinstance(system, str):
            text = system
        else:
            text = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in system
            )
        m = _DEFERRED_TOOLS_RE.search(text)
        if m:
            return [name.strip() for name in m.group(1).splitlines() if name.strip()]

    # ── Fallback: last user message ToolSearch system-reminder ──────────────
    if messages:
        for msg in reversed(messages):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role != "user":
                continue
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            text = _content_to_text(content)
            m = _TOOLSEARCH_DEFERRED_RE.search(text)
            if m:
                names = [n.strip() for n in m.group(1).splitlines() if n.strip()]
                # Filter to known CC workflow tools only for safety
                return [n for n in names if n in _CC_WORKFLOW_TOOL_NAMES]

    return []


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


def validate_tool_name_with_deferred_bypass(name: str, valid_names: set[str]) -> bool:
    """Validate tool name, but always allow known CC workflow tools.

    CC workflow tools are proxy-injected from the <available-deferred-tools>
    block in the system prompt. They may not be in request.tools at validation
    time (e.g. stripped by ToolAllowlistTransformer), but are always legitimate.

    Parity function: used by both streaming (stream_event.py) and non-streaming
    (converters.py) so validation semantics are identical in both paths.
    """
    if not valid_names:
        return True
    if not name or not isinstance(name, str):
        return False
    if name.strip() in _CC_WORKFLOW_TOOL_NAMES:
        return True
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
