# llm/stream_quality.py — Re-export shim (all logic now in llm/transformers/)
"""
Canonical homes for all quality functions:

  extract_response_text, score_anthropic_response, _validate_intent_outcome,
  analysis_quality_stream, _build_verification_feedback
    → llm/transformers/quality_refinement.py

  accumulate_stream, tracked_stream
    → llm/transformers/stream_event.py

This shim exists for backward compatibility so all callers continue to work.
"""
from llm.transformers.quality_refinement import (
    extract_response_text,
    score_anthropic_response,
    _validate_intent_outcome,
    analysis_quality_stream,
    analysis_quality_nonstream,
    _build_verification_feedback,
)
from llm.transformers.stream_event import (
    accumulate_stream,
    tracked_stream,
)

__all__ = [
    "extract_response_text",
    "score_anthropic_response",
    "_validate_intent_outcome",
    "analysis_quality_stream",
    "_build_verification_feedback",
    "accumulate_stream",
    "tracked_stream",
]
