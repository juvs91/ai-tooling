# config.py
"""
Centralized configuration — all env vars read once at startup.

Every setting the proxy uses flows through ProxyConfig.
Env var names are unchanged for backward compatibility with profile-envs/.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from llm.schemas import ProviderConfig


# ── Guardrails default (always injected) ──
_DEFAULT_GUARD = (
    "[proxy-guard] IMPORTANT: If you do not have tool access (filesystem/bash/etc.) "
    "or you were not given the file contents, do NOT guess or fabricate. "
    "Explain what you need (enable tools or paste the content) and proceed only with available info."
)


def _load_guard_system() -> str:
    """Load guardrails from file if GUARDRAILS_FILE is set, otherwise use default."""
    gf = os.environ.get("GUARDRAILS_FILE", "").strip()
    if gf and os.path.isfile(gf):
        try:
            with open(gf, "r") as f:
                extra = f.read().strip()
            if extra:
                return _DEFAULT_GUARD + "\n\n" + extra
        except Exception:
            pass
    return _DEFAULT_GUARD


# ── Dataclasses ──

@dataclass
class ProviderCredentials:
    openai_api_key: str
    openai_base_url: Optional[str]
    anthropic_api_key: Optional[str]
    anthropic_base_url: Optional[str]
    anthropic_endpoint_path: str = "/v1/messages"  # ENV: ANTHROPIC_ENDPOINT_PATH
    gemini_api_key: Optional[str] = None
    use_vertex_auth: bool = False
    vertex_project: str = ""
    vertex_location: str = ""

    @property
    def anthropic_litellm_api_base(self) -> Optional[str]:
        """Full URL for LiteLLM Anthropic calls.

        Set LITELLM_ANTHROPIC_DISABLE_URL_SUFFIX=true in the profile env so
        LiteLLM uses this URL as-is instead of appending /v1/messages.
        For non-standard endpoints (e.g. Kimi /coding/v1/messages), this
        produces the correct full URL from the two existing env vars.
        """
        if not self.anthropic_base_url:
            return None
        return self.anthropic_base_url + self.anthropic_endpoint_path


@dataclass
class RouteOverride:
    """Per-route provider override for cross-provider configs.

    When a model route (small/building) lives on a different provider
    than the primary, this holds its credentials and endpoint.
    Same pattern as AnalysisConfig and ProviderConfig (fallback chain).

    ENV: SMALL_PROVIDER, SMALL_API_KEY, SMALL_BASE_URL
         BUILDING_PROVIDER, BUILDING_API_KEY, BUILDING_BASE_URL
    """
    provider: str              # litellm prefix: "openai", "deepseek", etc.
    api_key: str
    base_url: Optional[str] = None
    context_window: int = 0    # 0 = use global MODEL_CONTEXT_WINDOW


@dataclass
class ModelRouting:
    preferred_provider: str        # "openai", "anthropic", "google"
    small_model: str
    big_model: str
    building_model: str            # defaults to big_model
    model_context_window: int      # 0 = disable scaling
    max_output_tokens: int
    reasoning_max_tokens: int      # cap for no_tools/reasoning models (0 = uncapped)
    # Per-route provider overrides (when model lives on a different provider)
    small_route: Optional[RouteOverride] = None
    building_route: Optional[RouteOverride] = None
    low_confidence_threshold: float = 0.65  # ENV: LOW_CONFIDENCE_THRESHOLD


@dataclass
class ClassifierConfig:
    model: str                     # "" = regex fallback
    api_key: str
    base_url: Optional[str]
    timeout: float                 # default: 3.0


@dataclass
class CompressorConfig:
    model: str                     # resolved: COMPRESSOR_MODEL || CLASSIFIER_MODEL
    api_key: str                   # resolved: COMPRESSOR_API_KEY || CLASSIFIER_API_KEY
    base_url: Optional[str]        # resolved: COMPRESSOR_BASE_URL || CLASSIFIER_BASE_URL
    keep_recent: int
    trigger_ratio: float
    fallback_model: Optional[str]
    fallback_api_key: Optional[str]
    fallback_base_url: Optional[str]
    # New parameters for context-window-aware compression
    message_threshold: int           # ENV: COMPRESSOR_MESSAGE_THRESHOLD (default: 20)
    max_messages_ratio: float        # ENV: COMPRESSOR_MAX_MESSAGES_RATIO (default: 0.85)
    max_tokens_ratio: float         # ENV: COMPRESSOR_MAX_TOKENS_RATIO (default: 0.85)
    tool_inflation_threshold: int    # ENV: COMPRESSOR_TOOL_INFLATION_THRESHOLD (default: 40)
    summary_trigger_ratio: float     # ENV: COMPRESSOR_SUMMARY_TRIGGER_RATIO (default: 0.60)
    recent_window_ratio: float        # ENV: COMPRESSOR_RECENT_WINDOW_RATIO (default: 0.40)


@dataclass
class AnalysisConfig:
    model: str                     # ENV: ANALYSIS_MODEL (override model for analysis)
    api_key: str                   # ENV: ANALYSIS_API_KEY
    base_url: Optional[str]        # ENV: ANALYSIS_BASE_URL
    max_tokens: int                # ENV: ANALYSIS_MAX_TOKENS (default: 16384)
    max_refinements: int           # ENV: ANALYSIS_MAX_REFINEMENTS (default: 1)
    quality_threshold: float       # ENV: ANALYSIS_QUALITY_THRESHOLD (default: 0.70)
    context_window: int = 0        # ENV: ANALYSIS_CONTEXT_WINDOW (default: 0 = use global)
    thinking_params: Optional[dict] = None  # ENV: ANALYSIS_THINKING_PARAMS (JSON body params for passthrough)
    synthesize_reads_fallback: int = 15     # ENV: ANALYSIS_SYNTHESIZE_READS_FALLBACK (safety net)
    llm_score_gate: bool = False            # ENV: ANALYSIS_LLM_SCORE_GATE (1=enable LLM second opinion for ambiguous scores)
    score_certainty_floor: float = 0.50     # ENV: ANALYSIS_SCORE_CERTAINTY_FLOOR (below this: skip LLM gate, always refine)
    grounding_threshold: float = 0.80       # ENV: GROUNDING_THRESHOLD
    grounding_refinement_enabled: bool = True  # ENV: GROUNDING_REFINEMENT
    stream_buffer_quality: bool = True       # ENV: STREAM_BUFFER_QUALITY


@dataclass
class PolicyConfig:
    tool_allowlist_raw: str
    policy_note_in_system: bool
    max_input_tokens: int
    hard_block_oversize: bool
    analysis_enforcement: bool
    tool_upgrade_threshold: int
    strip_reasoning: bool = False  # strip <reasoning> tags from final response
    guard_system: str = ""         # loaded from GUARDRAILS_FILE or default
    grounding_validation_enabled: bool = True  # ENV: GROUNDING_VALIDATION_ENABLED (default: True)
    multihop_grounding_enabled: bool = True   # ENV: MULTIHOP_GROUNDING_ENABLED (default: True)
    tool_exclude_raw: str = ""       # ENV: TOOL_EXCLUDE — comma-sep prefix patterns (e.g. mcp__playwright__*)
    tool_schema_max_desc: int = 200  # ENV: TOOL_SCHEMA_MAX_DESC — max chars per description (0=off)


@dataclass
class AdaptiveRoutingConfig:
    enabled: bool = True               # ENV: ADAPTIVE_ROUTING
    quality_fallback_threshold: float = 0.65  # ENV: ADAPTIVE_QUALITY_THRESHOLD
    min_sample_size: int = 5           # ENV: ADAPTIVE_MIN_SAMPLE
    quality_window_size: int = 20      # ENV: ADAPTIVE_WINDOW_SIZE
    min_quality_advantage: float = 0.10  # ENV: ADAPTIVE_MIN_ADVANTAGE


@dataclass
class ModelCosts:
    """Per-model cost rates for cost tracking.

    ENV: MODEL_COSTS="glm-4.7:0.38:0.38,MiniMax-M2.5:0.30:0.30,deepseek-chat:0.001:0.002"
    Format: "model:input_cost_per_M:output_cost_per_M,..."
    """
    rates: dict[str, tuple[float, float]] = field(default_factory=dict)

    def cost_usd(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD for given token counts."""
        # Try exact match, then strip provider prefix
        key = model.split("/")[-1] if "/" in model else model
        rate = self.rates.get(key) or self.rates.get(model)
        if not rate:
            return 0.0
        input_cost, output_cost = rate
        return (input_tokens * input_cost + output_tokens * output_cost) / 1_000_000


@dataclass
class ProviderQuirksConfig:
    """Per-provider tunable parameters. All values configurable via env vars."""
    kimi_max_temp: float = 0.8           # QUIRKS_KIMI_MAX_TEMP — clamp threshold
    kimi_clamp_temp: float = 0.6         # QUIRKS_KIMI_CLAMP_TEMP — value to clamp to
    deepseek_analysis_max_tokens: int = 8000  # QUIRKS_DEEPSEEK_ANALYSIS_MAX_TOKENS


@dataclass
class ProxyConfig:
    credentials: ProviderCredentials
    routing: ModelRouting
    classifier: ClassifierConfig
    compressor: CompressorConfig
    policy: PolicyConfig
    analysis: AnalysisConfig
    model_costs: ModelCosts
    max_retries: int
    retry_base_delay: float
    passthrough_disabled: bool     # ENV: PASSTHROUGH_DISABLED (default: False, auto-detect)
    passthrough_require_prefix: bool  # ENV: PASSTHROUGH_REQUIRE_PREFIX (default: True — bare model names skip passthrough)
    passthrough_timeout: float     # ENV: PASSTHROUGH_TIMEOUT (default: 120)
    passthrough_thinking_timeout: float  # ENV: PASSTHROUGH_THINKING_TIMEOUT (default: 300)
    thinking_max_input_chars: int  # ENV: THINKING_MAX_INPUT_CHARS (0 = no cap)
    adaptive: AdaptiveRoutingConfig = field(default_factory=AdaptiveRoutingConfig)
    quirks: ProviderQuirksConfig = field(default_factory=ProviderQuirksConfig)
    litellm_thinking_params: Optional[dict] = None  # Provider-specific thinking params for LiteLLM
    cache_enabled: bool = True
    cache_ttl: int = 60
    stream_extra_body: Optional[dict] = None
    # Safety nets
    max_turns: int = 300           # ENV: MAX_TURNS_PER_SESSION (0 = unlimited)
    session_cost_warning: float = 3.0   # ENV: SESSION_COST_WARNING_USD
    session_cost_limit: float = 5.0     # ENV: SESSION_COST_LIMIT_USD
    fallback_providers: list[ProviderConfig] = field(default_factory=list)


# ── Loaders ──

def _load_fallback_providers() -> list[ProviderConfig]:
    """Load FALLBACK_1_* through FALLBACK_9_* from env."""
    providers: list[ProviderConfig] = []
    for n in range(1, 10):
        prefix = f"FALLBACK_{n}_"
        provider = os.environ.get(f"{prefix}PROVIDER", "").strip()
        api_key = os.environ.get(f"{prefix}API_KEY", "").strip()
        if not provider or not api_key:
            break  # stop at first gap
        providers.append(ProviderConfig(
            name=f"fallback_{n}",
            provider_prefix=provider,
            api_key=api_key,
            base_url=os.environ.get(f"{prefix}BASE_URL", "").strip() or None,
            big_model=os.environ.get(f"{prefix}BIG_MODEL", "").strip(),
            small_model=(
                os.environ.get(f"{prefix}SMALL_MODEL", "").strip()
                or os.environ.get(f"{prefix}BIG_MODEL", "").strip()
            ),
            building_model=os.environ.get(f"{prefix}BUILDING_MODEL", "").strip() or None,
            context_window=int(os.environ.get(f"{prefix}CONTEXT_WINDOW", "0")),
        ))
    return providers


def _parse_stream_extra_body() -> Optional[dict]:
    """Parse STREAM_EXTRA_BODY JSON once at startup."""
    raw = os.environ.get("STREAM_EXTRA_BODY", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) and parsed else None
    except json.JSONDecodeError:
        return None


def _parse_thinking_params() -> Optional[dict]:
    """Parse ANALYSIS_THINKING_PARAMS JSON env var.

    Model-agnostic: any JSON body params to merge into passthrough requests
    during ANALYZING phase (e.g. {"thinking":{"type":"enabled"},"clear_thinking":false}).
    """
    raw = os.environ.get("ANALYSIS_THINKING_PARAMS", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) and parsed else None
    except json.JSONDecodeError:
        return None


def _parse_litellm_thinking_params() -> Optional[dict]:
    """Parse LITELLM_THINKING_PARAMS JSON env var.

    Provider-specific thinking configuration for LiteLLM providers.
    Example: {"deepseek": {"max_tokens": 8000}, "minimax": {"thinking": {"type": "enabled"}}}
    """
    raw = os.environ.get("LITELLM_THINKING_PARAMS", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) and parsed else None
    except json.JSONDecodeError:
        return None


def _parse_model_costs() -> ModelCosts:
    """Parse MODEL_COSTS env var: 'model:input:output,model:input:output,...'"""
    raw = os.environ.get("MODEL_COSTS", "").strip()
    rates: dict[str, tuple[float, float]] = {}
    if not raw:
        return ModelCosts(rates=rates)
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) == 3:
            model, inp, out = parts[0].strip(), parts[1].strip(), parts[2].strip()
            try:
                rates[model] = (float(inp), float(out))
            except ValueError:
                pass
    return ModelCosts(rates=rates)


def load_config() -> ProxyConfig:
    """Read all env vars once and return a fully-populated ProxyConfig."""
    # Helper for "strip or None" pattern
    def _env(key: str, default: str = "") -> str:
        return os.environ.get(key, default)

    def _env_stripped(key: str, default: str = "") -> str:
        return (_env(key, default) or "").strip()

    def _env_or_none(key: str) -> Optional[str]:
        v = (_env(key) or "").strip()
        return v or None

    # -- Models --
    small_model = _env_stripped("SMALL_MODEL", "cc-local:chat")
    big_model = _env_stripped("BIG_MODEL", small_model)
    building_model = _env_stripped("BUILDING_MODEL", big_model)

    # -- Classifier --
    classifier_model = _env_stripped("CLASSIFIER_MODEL")
    classifier_api_key = _env_stripped("CLASSIFIER_API_KEY")
    classifier_base_url = _env_or_none("CLASSIFIER_BASE_URL")

    # -- Compressor (falls back to classifier settings) --
    comp_model = _env_stripped("COMPRESSOR_MODEL") or classifier_model
    comp_key = _env_stripped("COMPRESSOR_API_KEY") or classifier_api_key
    comp_base = _env_or_none("COMPRESSOR_BASE_URL") or classifier_base_url

    # -- Analysis (reasoning model override for analysis requests) --
    analysis_model = _env_stripped("ANALYSIS_MODEL")
    analysis_api_key = _env_stripped("ANALYSIS_API_KEY")
    analysis_base_url = _env_or_none("ANALYSIS_BASE_URL")

    # -- Per-route overrides (cross-provider mixed configs) --
    def _load_route(prefix: str) -> Optional[RouteOverride]:
        provider = _env_stripped(f"{prefix}_PROVIDER")
        api_key = _env_stripped(f"{prefix}_API_KEY")
        if not api_key:
            return None
        return RouteOverride(
            provider=provider or "openai",
            api_key=api_key,
            base_url=_env_or_none(f"{prefix}_BASE_URL"),
            context_window=int(_env(f"{prefix}_CONTEXT_WINDOW", "0")),
        )

    small_route = _load_route("SMALL")
    building_route = _load_route("BUILDING")

    return ProxyConfig(
        credentials=ProviderCredentials(
            openai_api_key=_env("OPENAI_API_KEY", ""),
            openai_base_url=_env_or_none("OPENAI_BASE_URL"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY") or None,
            anthropic_base_url=_env_or_none("ANTHROPIC_BASE_URL"),
            anthropic_endpoint_path=_env("ANTHROPIC_ENDPOINT_PATH", "/v1/messages"),
            gemini_api_key=_env("GEMINI_API_KEY") or None,
            use_vertex_auth=_env("USE_VERTEX_AUTH", "False").lower() == "true",
            vertex_project=_env("VERTEX_PROJECT", "unset"),
            vertex_location=_env("VERTEX_LOCATION", "unset"),
        ),
        routing=ModelRouting(
            preferred_provider=_env("PREFERRED_PROVIDER", "openai").lower(),
            small_model=small_model,
            big_model=big_model,
            building_model=building_model,
            model_context_window=int(_env("MODEL_CONTEXT_WINDOW", "0")),
            max_output_tokens=int(_env("MAX_OUTPUT_TOKENS", "8192")),
            reasoning_max_tokens=int(_env("REASONING_MAX_TOKENS", "16000")),
            small_route=small_route,
            building_route=building_route,
            low_confidence_threshold=float(_env("LOW_CONFIDENCE_THRESHOLD", "0.65")),
        ),
        classifier=ClassifierConfig(
            model=classifier_model,
            api_key=classifier_api_key,
            base_url=classifier_base_url,
            timeout=float(_env("CLASSIFIER_TIMEOUT", "3.0")),
        ),
        compressor=CompressorConfig(
            model=comp_model,
            api_key=comp_key,
            base_url=comp_base,
            keep_recent=int(_env("COMPRESSOR_KEEP_RECENT", "10")),  # was 15
            trigger_ratio=float(_env("COMPRESSOR_TRIGGER_RATIO", "0.70")),  # was 0.85
            fallback_model=_env_or_none("COMPRESSOR_FALLBACK_MODEL"),
            fallback_api_key=_env_or_none("COMPRESSOR_FALLBACK_API_KEY"),
            fallback_base_url=_env_or_none("COMPRESSOR_FALLBACK_BASE_URL"),
            # New parameters for context-window-aware compression
            message_threshold=int(_env("COMPRESSOR_MESSAGE_THRESHOLD", "20")),
            max_messages_ratio=float(_env("COMPRESSOR_MAX_MESSAGES_RATIO", "0.85")),
            max_tokens_ratio=float(_env("COMPRESSOR_MAX_TOKENS_RATIO", "0.85")),
            tool_inflation_threshold=int(_env("COMPRESSOR_TOOL_INFLATION_THRESHOLD", "40")),
            summary_trigger_ratio=float(_env("COMPRESSOR_SUMMARY_TRIGGER_RATIO", "0.60")),
            recent_window_ratio=float(_env("COMPRESSOR_RECENT_WINDOW_RATIO", "0.40")),
        ),
        policy=PolicyConfig(
            tool_allowlist_raw=_env_stripped("TOOL_ALLOWLIST"),
            policy_note_in_system=_env_stripped("POLICY_NOTE_IN_SYSTEM", "1") == "1",
            max_input_tokens=int(_env("MAX_INPUT_TOKENS", "0")),
            hard_block_oversize=_env_stripped("HARD_BLOCK_OVERSIZE", "0") == "1",
            analysis_enforcement=_env_stripped("ANALYSIS_ENFORCEMENT", "0") == "1",
            tool_upgrade_threshold=int(_env("TOOL_UPGRADE_THRESHOLD", "5")),
            strip_reasoning=_env_stripped("STRIP_REASONING", "0") == "1",
            guard_system=_load_guard_system(),
            grounding_validation_enabled=_env_stripped("GROUNDING_VALIDATION_ENABLED", "1") == "1",
            tool_exclude_raw=_env_stripped("TOOL_EXCLUDE", ""),
            tool_schema_max_desc=int(_env("TOOL_SCHEMA_MAX_DESC", "200")),
        ),
        analysis=AnalysisConfig(
            model=analysis_model,
            api_key=analysis_api_key,
            base_url=analysis_base_url,
            max_tokens=int(_env("ANALYSIS_MAX_TOKENS", "16384")),
            max_refinements=int(_env("ANALYSIS_MAX_REFINEMENTS", "1")),
            quality_threshold=float(_env("ANALYSIS_QUALITY_THRESHOLD", "0.70")),
            context_window=int(_env("ANALYSIS_CONTEXT_WINDOW", "0")),
            thinking_params=_parse_thinking_params(),
            synthesize_reads_fallback=int(_env("ANALYSIS_SYNTHESIZE_READS_FALLBACK", "15")),
            llm_score_gate=_env_stripped("ANALYSIS_LLM_SCORE_GATE", "0") == "1",
            score_certainty_floor=float(_env("ANALYSIS_SCORE_CERTAINTY_FLOOR", "0.50")),
            grounding_threshold=float(_env("GROUNDING_THRESHOLD", "0.80")),
            grounding_refinement_enabled=_env_stripped("GROUNDING_REFINEMENT", "true") == "true",
            stream_buffer_quality=_env_stripped("STREAM_BUFFER_QUALITY", "true") == "true",
        ),
        model_costs=_parse_model_costs(),
        adaptive=AdaptiveRoutingConfig(
            enabled=_env_stripped("ADAPTIVE_ROUTING", "true") == "true",
            quality_fallback_threshold=float(_env("ADAPTIVE_QUALITY_THRESHOLD", "0.65")),
            min_sample_size=int(_env("ADAPTIVE_MIN_SAMPLE", "5")),
            quality_window_size=int(_env("ADAPTIVE_WINDOW_SIZE", "20")),
            min_quality_advantage=float(_env("ADAPTIVE_MIN_ADVANTAGE", "0.10")),
        ),
        passthrough_disabled=_env("PASSTHROUGH_DISABLED", "0") == "1",
        passthrough_require_prefix=_env("PASSTHROUGH_REQUIRE_PREFIX", "1") == "1",
        passthrough_timeout=float(_env("PASSTHROUGH_TIMEOUT", "120")),
        passthrough_thinking_timeout=float(_env("PASSTHROUGH_THINKING_TIMEOUT", "300")),
        thinking_max_input_chars=int(_env("THINKING_MAX_INPUT_CHARS", "0")),
        quirks=ProviderQuirksConfig(
            kimi_max_temp=float(_env("QUIRKS_KIMI_MAX_TEMP", "0.8")),
            kimi_clamp_temp=float(_env("QUIRKS_KIMI_CLAMP_TEMP", "0.6")),
            deepseek_analysis_max_tokens=int(_env("QUIRKS_DEEPSEEK_ANALYSIS_MAX_TOKENS", "8000")),
        ),
        litellm_thinking_params=_parse_litellm_thinking_params(),
        max_retries=int(_env("MAX_RETRIES", "5")),
        retry_base_delay=float(_env("RETRY_BASE_DELAY", "1.0")),
        cache_enabled=_env_stripped("CACHE_ENABLED", "0") == "1",
        cache_ttl=int(_env("CACHE_TTL", "60")),
        stream_extra_body=_parse_stream_extra_body(),
        max_turns=int(_env("MAX_TURNS_PER_SESSION", "300")),
        session_cost_warning=float(_env("SESSION_COST_WARNING_USD", "3.0")),
        session_cost_limit=float(_env("SESSION_COST_LIMIT_USD", "5.0")),
        fallback_providers=_load_fallback_providers(),
    )
