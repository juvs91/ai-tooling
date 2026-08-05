# llm/state_assertion_rules/exploration_grounding.py
"""exploration_grounding rule (ADR-0040, F-001 direction 3).

Closes a gap ADR-0033 explicitly admits: "the evidence check is binary and
topic-blind" — a Grep/Glob/Read having happened ANYWHERE in the conversation
counts as "verified," regardless of whether the model's current reasoning
actually engages with what it found. This rule adds a lightweight, genuinely
topic-aware check: when the model edits a file in the CURRENT response, does
its own reasoning text in that same response share any distinctive vocabulary
with the actual content read from that file
(ctx.code_snippet_cache — populated by GroundingValidatorTransformer, which
always runs immediately before this rule in build_response_pipeline,
proxy/proxy.py)?

This does NOT replace grounding_validator.py's existing "unread edit target"
check (Step 2.5, _extract_edit_paths vs evidence_map) — that answers a
strictly weaker question ("was the file ever read at all?"). This rule
answers the harder one this incident's real damage was about: the source
incident's `contract_schema.py`/`sources.py` rewrites had the right file names
and structure but the actual central logic from the plan was absent — code
that read as plausible but never engaged with what was actually specified.
A file can be read AND edited while the surrounding reasoning never mentions
anything from it; that's the drift this rule targets.
"""
from __future__ import annotations

import re

from llm.state_assertion import AssertionFinding

_WRITE_TOOL_NAMES = frozenset({"Edit", "MultiEdit", "Write"})
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")  # identifiers/words, len >= 4
_STOPWORDS = frozenset({
    "this", "that", "with", "from", "have", "will", "file", "code", "function",
    "class", "value", "should", "which", "using", "these", "those", "into",
    "then", "here", "there", "were", "your", "also", "when", "what", "text",
})


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS}


def _response_text(content) -> str:
    if not isinstance(content, list):
        return ""
    return "\n".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _edited_paths_this_turn(content) -> set[str]:
    if not isinstance(content, list):
        return set()
    paths = set()
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") not in _WRITE_TOOL_NAMES:
            continue
        path = (block.get("input") or {}).get("file_path", "")
        if path:
            paths.add(path)
    return paths


class ExplorationGroundingRule:
    """Flags edits whose surrounding reasoning shares no vocabulary with the
    actual content previously read from the edited file."""

    id = "exploration_grounding"
    phase = "response"
    agnostic = True

    # Deliberately lax — this is a coarse, deterministic proxy for "did the
    # reasoning engage with the evidence," not a semantic sufficiency check
    # (same "weak signal, hedge accordingly" framing ADR-0033 uses). One
    # shared distinctive token is enough to NOT flag; the goal is only to
    # catch the zero-overlap case, not to grade quality of engagement.
    MIN_SHARED_TOKENS = 1

    def evaluate(self, *, content, messages, ctx, tools, model, session_snapshot):
        edited_paths = _edited_paths_this_turn(content)
        if not edited_paths:
            return []

        snippet_cache = getattr(ctx, "code_snippet_cache", None) or {}
        reasoning_tokens = _tokens(_response_text(content))
        if not reasoning_tokens:
            # Tool-only response, no prose alongside the edit — nothing to
            # check topic-overlap against; not this rule's territory.
            return []

        findings: list[AssertionFinding] = []
        for path in sorted(edited_paths):
            snippet = snippet_cache.get(path)
            if not snippet:
                # File was never read — grounding_validator.py's Step 2.5
                # ("unread edit target") already flags this, separately.
                continue
            snippet_tokens = _tokens(snippet)
            if not snippet_tokens:
                continue
            shared = reasoning_tokens & snippet_tokens
            if len(shared) < self.MIN_SHARED_TOKENS:
                findings.append(AssertionFinding(
                    rule_id=self.id,
                    verdict="unverifiable",
                    evidence_snippet=snippet[:200],
                    subject=path,
                    correction_note=(
                        f"You edited '{path}' but your explanation doesn't reference "
                        "anything from the actual file content you read for it — "
                        "verify the edit reflects what's really there, not an assumption."
                    ),
                    severity="nudge",
                ))
        return findings


# No import-time registration here — see llm/state_assertion_rules/__init__.py's
# ACTIVE_RULE_CLASSES + build_active_rules() for the single, explicit construction site.
