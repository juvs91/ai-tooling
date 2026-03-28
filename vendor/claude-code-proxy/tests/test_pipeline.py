# tests/test_pipeline.py
"""Tests for llm/pipeline.py — Pipeline, Transformer ABC, TransformContext."""
import pytest
from dataclasses import fields
from typing import Any

from llm.pipeline import Pipeline, Transformer, TransformContext


# ── Concrete test transformer ───────────────────────────────────────

class _RecordingTransformer(Transformer):
    """Transformer that records calls for assertions."""

    def __init__(self, label: str, side_effect=None):
        self._label = label
        self._side_effect = side_effect
        self.calls: list[tuple] = []

    @property
    def name(self) -> str:
        return self._label

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        self.calls.append((request, ctx))
        if self._side_effect:
            self._side_effect(request, ctx)


class _FailingTransformer(Transformer):
    @property
    def name(self) -> str:
        return "failing"

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        raise ValueError("transformer exploded")


# ── TransformContext ────────────────────────────────────────────────

class TestTransformContext:

    def test_defaults(self):
        ctx = TransformContext()
        assert ctx.raw_body == b""
        assert ctx.intent == "CHAT"
        assert ctx.is_analysis is False
        assert ctx.approx_tokens == 0
        assert ctx.dropped_tools == []
        assert ctx.was_compressed is False
        assert ctx.litellm_request == {}

    def test_custom_init(self):
        ctx = TransformContext(raw_body=b"hello", intent="BUILDING", is_analysis=True)
        assert ctx.raw_body == b"hello"
        assert ctx.intent == "BUILDING"
        assert ctx.is_analysis is True

    def test_mutable_defaults_are_independent(self):
        """Each instance gets its own list/dict."""
        a = TransformContext()
        b = TransformContext()
        a.dropped_tools.append("Read")
        a.litellm_request["model"] = "x"
        assert b.dropped_tools == []
        assert b.litellm_request == {}

    def test_all_fields_documented(self):
        """Ensure no unnamed fields slip in."""
        names = {f.name for f in fields(TransformContext)}
        expected = {
            "raw_body", "intent", "phase", "is_analysis", "approx_tokens",
            "dropped_tools", "was_compressed", "litellm_request",
            "route_override", "effective_context_window",
            "quality_score", "quality_issues", "refinement_attempt",
            "analysis_phase", "analysis_read_count",
            "tools", "session_id",
            "extracted_tool_calls", "xml_tool_buffer",
            # Grounding fields
            "evidence_links", "citation_map", "grounding_score",
            "grounding_issues", "evidence_graph", "code_snippet_cache",
            # Plan mode lock (set by IntentClassifierTransformer)
            "plan_mode_active",
            # P3 — confidence scoring
            "intent_confidence", "secondary_intent",
            # P2 — adaptive routing
            "adaptive_routing_enabled", "adaptive_routing_used",
            "adaptive_routing_reason", "model_quality_history",
            # P1 — stream buffering
            "stream_finish_reason", "stream_input_tokens", "stream_output_tokens",
        }
        assert names == expected


# ── Pipeline ────────────────────────────────────────────────────────

class TestPipeline:

    @pytest.mark.asyncio
    async def test_empty_pipeline(self):
        """An empty pipeline is valid and does nothing."""
        pipe = Pipeline([])
        ctx = TransformContext()
        await pipe.process({}, ctx)
        assert pipe.transformer_names == []

    @pytest.mark.asyncio
    async def test_single_transformer(self):
        t = _RecordingTransformer("one")
        pipe = Pipeline([t])
        req = {"data": 1}
        ctx = TransformContext()
        await pipe.process(req, ctx)
        assert len(t.calls) == 1
        assert t.calls[0] == (req, ctx)

    @pytest.mark.asyncio
    async def test_order_preserved(self):
        """Transformers run in insertion order."""
        order = []
        a = _RecordingTransformer("a", side_effect=lambda r, c: order.append("a"))
        b = _RecordingTransformer("b", side_effect=lambda r, c: order.append("b"))
        c = _RecordingTransformer("c", side_effect=lambda r, c: order.append("c"))
        pipe = Pipeline([a, b, c])
        await pipe.process({}, TransformContext())
        assert order == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_context_shared_across_transformers(self):
        """Transformer A writes ctx.intent; Transformer B sees it."""
        def set_intent(req, ctx):
            ctx.intent = "BUILDING"

        def check_intent(req, ctx):
            assert ctx.intent == "BUILDING"

        a = _RecordingTransformer("setter", side_effect=set_intent)
        b = _RecordingTransformer("checker", side_effect=check_intent)
        pipe = Pipeline([a, b])
        await pipe.process({}, TransformContext())

    @pytest.mark.asyncio
    async def test_request_mutation_visible_downstream(self):
        """In-place mutation on request is visible to next transformer."""
        def mutate(req, ctx):
            req["mutated"] = True

        def check(req, ctx):
            assert req.get("mutated") is True

        a = _RecordingTransformer("mutator", side_effect=mutate)
        b = _RecordingTransformer("verifier", side_effect=check)
        pipe = Pipeline([a, b])
        await pipe.process({}, TransformContext())

    @pytest.mark.asyncio
    async def test_exception_stops_pipeline(self):
        """If a transformer raises, subsequent ones don't run."""
        ran_after = []
        after = _RecordingTransformer("after", side_effect=lambda r, c: ran_after.append(True))
        pipe = Pipeline([_FailingTransformer(), after])
        with pytest.raises(ValueError, match="transformer exploded"):
            await pipe.process({}, TransformContext())
        assert ran_after == []

    def test_transformer_names(self):
        a = _RecordingTransformer("alpha")
        b = _RecordingTransformer("beta")
        pipe = Pipeline([a, b])
        assert pipe.transformer_names == ["alpha", "beta"]
