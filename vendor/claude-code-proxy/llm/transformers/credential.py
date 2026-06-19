# llm/transformers/credential.py
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from llm.pipeline import Transformer, TransformContext
from config import ProviderCredentials, AnalysisConfig


def _inject_credentials(
    litellm_request: dict,
    *,
    model: str,
    creds: ProviderCredentials,
) -> None:
    """Inject provider credentials into a litellm request dict."""
    if model.startswith("openai/"):
        litellm_request["api_key"] = creds.openai_api_key
        if creds.openai_base_url:
            litellm_request["api_base"] = creds.openai_base_url
    elif model.startswith("gemini/"):
        if creds.use_vertex_auth:
            litellm_request["vertex_project"] = creds.vertex_project
            litellm_request["vertex_location"] = creds.vertex_location
            litellm_request["custom_llm_provider"] = "vertex_ai"
        else:
            litellm_request["api_key"] = creds.gemini_api_key
    else:  # anthropic/ prefix or bare model names
        litellm_request["api_key"] = creds.anthropic_api_key
        if creds.anthropic_litellm_api_base:
            litellm_request["api_base"] = creds.anthropic_litellm_api_base


class CredentialTransformer(Transformer):
    """Inject provider credentials based on model prefix."""

    @property
    def name(self) -> str:
        return "credential"

    def __init__(
        self,
        credentials_cfg: ProviderCredentials,
        analysis_cfg: AnalysisConfig | None = None,
    ) -> None:
        self._creds = credentials_cfg
        self._analysis = analysis_cfg

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        model = str(getattr(request, "model", "") or "")

        # Analysis override: use dedicated credentials only during SYNTHESIZING phase
        # (ANALYZING uses BIG_MODEL with primary credentials)
        if ctx.analysis_phase == "SYNTHESIZING" and self._analysis and self._analysis.model and self._analysis.api_key:
            ctx.litellm_request["api_key"] = self._analysis.api_key
            if self._analysis.base_url:
                ctx.litellm_request["api_base"] = self._analysis.base_url
            logger.info("[credential] Using analysis credentials for model=%s", model)
            return

        # Route override: cross-provider model (set by ModelRouterTransformer)
        if ctx.route_override:
            ctx.litellm_request["api_key"] = ctx.route_override.api_key
            if ctx.route_override.base_url:
                ctx.litellm_request["api_base"] = ctx.route_override.base_url
            logger.info("[credential] Using route override for model=%s", model)
            return

        _inject_credentials(ctx.litellm_request, model=model, creds=self._creds)
