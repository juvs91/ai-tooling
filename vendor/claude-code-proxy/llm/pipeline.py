# llm/pipeline.py — Transformer pipeline for composable request processing
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from config import RouteOverride


@dataclass
class TransformContext:
    """Shared state flowing through the transformer chain.

    Transformers read/write fields here instead of using side channels.
    This replaces the scattered local variables in apply_policy_and_routing()
    and the implicit state passed between server.py and proxy.py.
    """
    raw_body: bytes = b""

    # Set by IntentClassifierTransformer
    intent: str = "CHAT"
    phase: str = "EXECUTE"  # "EXPLORE" | "PLAN" | "EXECUTE"
    is_analysis: bool = False
    # Analysis phase state machine: NONE → ANALYZING → SYNTHESIZING → DONE
    # Replaces the sticky is_analysis boolean for routing decisions.
    # is_analysis remains for backward compat (guardrail prompts, etc.)
    analysis_phase: str = "NONE"  # NONE|ANALYZING|SYNTHESIZING|DONE
    analysis_read_count: int = 0

    # Set by IntentClassifierTransformer — authoritative plan mode signal.
    # True when EnterPlanMode was called in recent history WITHOUT subsequent ExitPlanMode.
    # Computed once early in the pipeline; all downstream transformers read this field
    # instead of independently re-deriving plan mode state from history.
    plan_mode_active: bool = False

    # Set by TokenCapTransformer
    approx_tokens: int = 0

    # Set by ToolAllowlistTransformer
    dropped_tools: list[str] = field(default_factory=list)

    # Set by CompressionTransformer (operates on litellm_request)
    was_compressed: bool = False

    # Populated by execution layer after convert_anthropic_to_litellm()
    litellm_request: dict = field(default_factory=dict)

    # Set by Session Management (Phase 3 Enhancement)
    session_id: str = field(default="")
    route_override: Optional["RouteOverride"] = None

    # Set by ModelRouterTransformer: resolved context window for the routed model
    # Used by CompressionTransformer, token scaling, and max_tokens recalculation
    effective_context_window: int = 0

    # Set during request phase - passed to response transformers
    # Original tools from request, needed for tool validation during extraction
    tools: list | None = None

    # Set by quality evaluation (refinement loop)
    quality_score: float = 1.0
    quality_issues: list[str] = field(default_factory=list)
    refinement_attempt: int = 0

    # Set by UniversalToolExtractionTransformer (response pipeline)
    # Stores XML-extracted tool calls so they can be added back to response content
    extracted_tool_calls: list = field(default_factory=list)
    xml_tool_buffer: Any = None

    # Set by GroundingValidatorTransformer (response pipeline)
    # Grounding state for multi-hop evidence tracking
    evidence_links: dict[str, list[str]] = field(default_factory=dict)
    # Maps claim → list of (file_path:line, code_snippet) tuples

    citation_map: dict[str, str] = field(default_factory=dict)
    # Maps citation → file_path (normalized)

    grounding_score: float = 1.0
    # 0.0-1.0: percentage of claims with verified evidence

    grounding_issues: list[str] = field(default_factory=list)
    # Specific grounding failures (e.g., "citation points to nonexistent file")

    evidence_graph: dict[str, dict] = field(default_factory=dict)
    # Multi-hop tracking: entity → {related_entities, citations, code_snippets}

    code_snippet_cache: dict[str, str] = field(default_factory=dict)
    # Cache of code snippets from tool results for verification
    # Key: file_path → Value: relevant code snippet (first 500 chars)

    # Set by IntentClassifierTransformer — True when PROXY_SESSION_MODE: ralph
    # detected in system prompt. Downstream transformers use this to suppress
    # AskUserQuestion calls and make autonomous best-effort decisions.
    ralph_mode: bool = False

    # Set by IntentClassifierTransformer (P3 — confidence scoring)
    intent_confidence: float = 1.0    # 1.0 = not evaluated (safe default)
    secondary_intent: str = ""        # secondary intent for multi-task requests

    # Set by AdaptiveContextTransformer (P2 — adaptive routing)
    adaptive_routing_enabled: bool = False
    adaptive_routing_used: bool = False
    adaptive_routing_reason: str = ""
    model_quality_history: dict = field(default_factory=dict)

    # Set by handle_streaming / stream_response_pipeline (P1)
    stream_finish_reason: str = "end_turn"
    stream_input_tokens: int = 0
    stream_output_tokens: int = 0


class Transformer(ABC):
    """Single-responsibility request modifier.

    Each transformer reads from TransformContext and may mutate
    the request object and/or the context.  Transformers run in
    order; each sees the output of the previous one.

    Convention:
    - Read config from self (injected at construction)
    - Read shared state from ctx
    - Write shared state to ctx
    - Mutate request_obj in-place (matching current proxy.py behavior)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for logging, e.g. 'guardrail'."""
        ...

    @abstractmethod
    async def transform(self, request: Any, ctx: TransformContext) -> None:
        """Transform the request in-place.  Write results to ctx."""
        ...


class Pipeline:
    """Ordered chain of transformers."""

    def __init__(self, transformers: list[Transformer]) -> None:
        self._transformers = transformers

    async def process(self, request: Any, ctx: TransformContext) -> None:
        """Run all transformers in order, mutating request and ctx."""
        for t in self._transformers:
            try:
                await t.transform(request, ctx)
            except Exception as e:
                print(f"[pipeline] Transformer '{t.name}' failed: {type(e).__name__}: {e}")
                raise

    @property
    def transformer_names(self) -> list[str]:
        return [t.name for t in self._transformers]
