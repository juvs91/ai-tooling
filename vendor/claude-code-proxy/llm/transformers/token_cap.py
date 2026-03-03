# llm/transformers/token_cap.py
from __future__ import annotations

from typing import Any, Optional

from llm.pipeline import Transformer, TransformContext
from utils.utils import approx_tokens_from_bytes
from config import PolicyConfig


def provider_cap_for_base_url(base_url: Optional[str]) -> int:
    if not base_url:
        return 0
    b = base_url.lower()
    if "api.groq.com" in b or "groq.com" in b:
        return 5500
    if "11434" in b:
        return 25000
    return 0


class TokenCapTransformer(Transformer):
    """Check token limits \u2014 provider-specific and hard cap."""

    @property
    def name(self) -> str:
        return "token_cap"

    def __init__(self, policy_cfg: PolicyConfig, base_url: Optional[str]) -> None:
        self._policy = policy_cfg
        self._base_url = base_url

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        ctx.approx_tokens = approx_tokens_from_bytes(ctx.raw_body)

        # Provider-specific cap (Groq=5500, Ollama=25000)
        cap = provider_cap_for_base_url(self._base_url)
        if cap and ctx.approx_tokens > cap:
            msg = (
                f"[proxy-policy] Provider cap exceeded: approx_tokens={ctx.approx_tokens} > cap={cap} "
                f"(base_url={self._base_url}). Reduce context or use another provider."
            )
            if self._policy.hard_block_oversize:
                raise ValueError(msg)

        # Hard cap
        if self._policy.max_input_tokens > 0 and ctx.approx_tokens > self._policy.max_input_tokens:
            msg = (
                f"[proxy-policy] Oversize request: approx_tokens={ctx.approx_tokens} > "
                f"MAX_INPUT_TOKENS={self._policy.max_input_tokens}. Reduce workspace/context."
            )
            if self._policy.hard_block_oversize:
                raise ValueError(msg)
