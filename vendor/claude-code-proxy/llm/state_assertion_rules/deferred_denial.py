# llm/state_assertion_rules/deferred_denial.py
"""deferred_denial rule (ADR-0038) — first rule registered on the
state-assertion framework (ADR-0036).

Root cause (docs/adr/ADR-0037-eager-plan-mode-tools-fragile-models.md,
docs/knowledge/kimi-tool-orchestration-context.md): a model concludes a tool
"doesn't exist" or "isn't connected" when it's actually a deferred tool the
model simply hasn't queried for via ToolSearch. Confirmed via a real
7847-line transcript: the model searched for `mcp__playwright__playwright_get`,
didn't find it in its visible tool list, concluded "not connected", and called
BigQuery tools by mistake instead — and, ~4000 lines later, reasoned "There's
an EnterPlanMode tool? Not listed... I don't have EnterPlanMode tool" despite
having read a full tutorial on deferred tools/ToolSearch earlier in the same
conversation. This rule detects that exact denial pattern in the model's own
response text and, only when the named tool is one the proxy actually knows
about (never a heuristic guess), injects a deterministic correction.
"""
from __future__ import annotations

import re

from llm.state_assertion import AssertionFinding
from utils.tool_utils import _CC_WORKFLOW_TOOL_NAMES, _MCP_TOOL_RE

# Sentence-level co-occurrence — mirrors _extract_completion_claims/
# _extract_generality_claims (grounding_validator.py, ADR-0031/0033): both a
# denial phrase AND a tool-name token must appear in the SAME sentence, to
# avoid false positives like "WebFetch does exist, I already used it".
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ])')

_DENIAL_PHRASE_RE = re.compile(
    r"\b(I don'?t have|I do not have|don'?t have (?:that|this|the) tool|"
    r"isn'?t (?:listed|available|connected)|is not (?:listed|available|connected)|"
    r"not listed|doesn'?t exist|does not exist|"
    r"no tengo (?:esa|esta|la|una) (?:tool|herramienta)|"
    r"no (?:cuento|dispongo) con (?:esa|esta) (?:tool|herramienta)|"
    r"no est[aá] (?:conectad[oa]|disponible|listad[oa])|"
    r"no existe (?:esa|esta|la) (?:tool|herramienta))\b",
    re.IGNORECASE,
)

# Every deferred tool name this rule can recognize is an EXACT, known string
# (the static CC catalog, or this session's cached <available-deferred-tools>
# list, or an mcp__ prefixed name) — never a generic capitalization heuristic.
# This keeps the false-positive rate at the same level as ADR-0031/0033's
# claim-detection design: the rule only fires when the tool provably exists.
_MCP_CANDIDATE_RE = re.compile(r"\bmcp__[^\s'\"]+__[^\s'\"]+\b")


def _extract_response_text(content) -> str:
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _known_tool_names(session_snapshot: dict) -> set[str]:
    """Static CC workflow catalog + this session's cached deferred-tools list
    (which may include MCP tool names seen this session, beyond the static
    frozenset — utils/tool_utils.py's known, documented gap)."""
    names = set(_CC_WORKFLOW_TOOL_NAMES)
    names.update(session_snapshot.get("deferred_tool_names") or [])
    return names


class DeferredDenialRule:
    """Detects a denial of a tool that the proxy knows actually exists."""

    id = "deferred_denial"
    phase = "response"
    agnostic = True  # cheap, correct-by-construction nudge — helpful for any model

    def evaluate(self, *, content, messages, ctx, tools, model, session_snapshot):
        text = _extract_response_text(content)
        if not text:
            return []

        known_static = _known_tool_names(session_snapshot)
        findings: list[AssertionFinding] = []
        seen_subjects: set[str] = set()  # one finding per subject per response

        for sentence in _SENTENCE_SPLIT_RE.split(text):
            if not _DENIAL_PHRASE_RE.search(sentence):
                continue

            candidates: set[str] = set()
            # Exact matches against the known static/session catalog.
            for name in known_static:
                if re.search(rf"\b{re.escape(name)}\b", sentence):
                    candidates.add(name)
            # mcp__server__tool shaped tokens — validated by the same regex
            # the proxy itself uses to recognize legitimate MCP tools
            # (validate_tool_name_with_deferred_bypass, utils/tool_utils.py).
            for match in _MCP_CANDIDATE_RE.finditer(sentence):
                token = match.group(0)
                if _MCP_TOOL_RE.match(token):
                    candidates.add(token)

            for tool_name in candidates - seen_subjects:
                seen_subjects.add(tool_name)
                findings.append(AssertionFinding(
                    rule_id=self.id,
                    verdict="contradicted",
                    evidence_snippet=sentence.strip()[:200],
                    subject=tool_name,
                    correction_note=(
                        f"'{tool_name}' is a deferred tool — it DOES exist. "
                        f"Call ToolSearch('select:{tool_name}') before concluding "
                        "it isn't available."
                    ),
                    severity="nudge",
                ))

        return findings


# No import-time registration here — see llm/state_assertion_rules/__init__.py's
# ACTIVE_RULE_CLASSES + build_active_rules() for the single, explicit construction site.
