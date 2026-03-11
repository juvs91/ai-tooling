# llm/streaming.py
"""Re-export shim — all streaming logic now lives in llm/transformers/stream_event.py.

This module exists for backward compatibility: all callers of
``from llm.streaming import handle_streaming`` continue to work unchanged.
"""
from llm.transformers.stream_event import (
    # Public entry points
    handle_streaming,
    passthrough_xml_tool_extraction,
    # Classes
    _ReasoningStripper,
    _StreamCtx,
    # Helpers (used by tests and stream_quality.py)
    _strip_think_tags,
    _close_json_brackets,
    _has_truncation_artifacts,
    _compute_repair_suffix,
    _warn_empty_tool_values,
    _emit_tool_use_block,
    _close_text_block,
    _emit_text_segment,
    _emit_xml_tool,
    _process_buffer_segments,
    _flush_xml_buffer,
    _recover_incomplete_tool,
    _close_native_tool_blocks,
    _process_reasoning_buffer,
    _compute_stream_stop_reason,
    _emit_stream_end,
    _estimate_output_tokens,
    # Constants
    _THINK_TAG_RE,
    _DANGEROUS_EMPTY_KEYS,
)

# Tool utilities re-exported so tests can patch llm.streaming.<name>
from utils.tool_utils import (
    is_no_tools_model,
    build_valid_tool_names as _build_valid_tool_names,
    validate_tool_name,
)

__all__ = [
    "handle_streaming",
    "passthrough_xml_tool_extraction",
    "_ReasoningStripper",
    "_StreamCtx",
    "_strip_think_tags",
    "_close_json_brackets",
    "_has_truncation_artifacts",
    "_compute_repair_suffix",
    "_warn_empty_tool_values",
    "_emit_tool_use_block",
    "_close_text_block",
    "_emit_text_segment",
    "_emit_xml_tool",
    "_process_buffer_segments",
    "_flush_xml_buffer",
    "_recover_incomplete_tool",
    "_close_native_tool_blocks",
    "_process_reasoning_buffer",
    "_compute_stream_stop_reason",
    "_emit_stream_end",
    "_estimate_output_tokens",
    "_THINK_TAG_RE",
    "_DANGEROUS_EMPTY_KEYS",
    "is_no_tools_model",
    "_build_valid_tool_names",
    "validate_tool_name",
]
