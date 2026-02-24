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
    gemini_api_key: Optional[str]
    use_vertex_auth: bool
    vertex_project: str
    vertex_location: str


@dataclass
class ModelRouting:
    preferred_provider: str        # "openai", "anthropic", "google"
    small_model: str
    big_model: str
    building_model: str            # defaults to big_model
    model_context_window: int      # 0 = disable scaling
    max_output_tokens: int


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


@dataclass
class PolicyConfig:
    tool_allowlist_raw: str
    policy_note_in_system: bool
    max_input_tokens: int
    hard_block_oversize: bool
    analysis_enforcement: bool
    tool_upgrade_threshold: int
    guard_system: str              # loaded from GUARDRAILS_FILE or default


@dataclass
class ProxyConfig:
    credentials: ProviderCredentials
    routing: ModelRouting
    classifier: ClassifierConfig
    compressor: CompressorConfig
    policy: PolicyConfig
    max_retries: int
    retry_base_delay: float
    cache_enabled: bool
    cache_ttl: int
    stream_extra_body: Optional[dict]
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

    return ProxyConfig(
        credentials=ProviderCredentials(
            openai_api_key=_env("OPENAI_API_KEY", ""),
            openai_base_url=_env_or_none("OPENAI_BASE_URL"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY") or None,
            anthropic_base_url=_env_or_none("ANTHROPIC_BASE_URL"),
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
            keep_recent=int(_env("COMPRESSOR_KEEP_RECENT", "15")),
            trigger_ratio=float(_env("COMPRESSOR_TRIGGER_RATIO", "0.85")),
            fallback_model=_env_or_none("COMPRESSOR_FALLBACK_MODEL"),
            fallback_api_key=_env_or_none("COMPRESSOR_FALLBACK_API_KEY"),
            fallback_base_url=_env_or_none("COMPRESSOR_FALLBACK_BASE_URL"),
        ),
        policy=PolicyConfig(
            tool_allowlist_raw=_env_stripped("TOOL_ALLOWLIST"),
            policy_note_in_system=_env_stripped("POLICY_NOTE_IN_SYSTEM", "1") == "1",
            max_input_tokens=int(_env("MAX_INPUT_TOKENS", "0")),
            hard_block_oversize=_env_stripped("HARD_BLOCK_OVERSIZE", "0") == "1",
            analysis_enforcement=_env_stripped("ANALYSIS_ENFORCEMENT", "0") == "1",
            tool_upgrade_threshold=int(_env("TOOL_UPGRADE_THRESHOLD", "5")),
            guard_system=_load_guard_system(),
        ),
        max_retries=int(_env("MAX_RETRIES", "5")),
        retry_base_delay=float(_env("RETRY_BASE_DELAY", "1.0")),
        cache_enabled=_env_stripped("CACHE_ENABLED", "0") == "1",
        cache_ttl=int(_env("CACHE_TTL", "60")),
        stream_extra_body=_parse_stream_extra_body(),
        fallback_providers=_load_fallback_providers(),
    )
