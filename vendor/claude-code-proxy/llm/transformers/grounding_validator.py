"""
Grounding Validator Transformer

Validates citations in model output against actual tool results.
Injects code snippets into system prompt for claim verification.
Prevents hallucinations by ensuring all claims have verified evidence.

Priority 4 enhancement: loads historically-read files from session cache so
citations to files read before a compression boundary still validate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os as _os
import re
import time
from typing import Any

from llm.pipeline import Transformer, TransformContext
from utils.utils import bget, ensure_system_note

logger = logging.getLogger(__name__)

# Citation pattern: (file.py:123) or [file.py:123] or file.py:123
_CITATION_PATTERN = re.compile(r'[\[\(]([\w/.-]+\.\w+:\d+)[\)\]]')
# Extract file path from citation
_FILE_FROM_CITATION = re.compile(r'([\w/.-]+\.\w+):\d+')

# Tools that write to files — their targets must have been read first
_WRITE_TOOL_NAMES = frozenset({"Edit", "MultiEdit", "Write"})


def _extract_edit_paths(response_content: list) -> set:
    """Return paths of existing files targeted by Write/Edit/MultiEdit tool calls."""
    return {
        path
        for block in (response_content or [])
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") in _WRITE_TOOL_NAMES
        and (path := (block.get("input") or {}).get("file_path", ""))
        # New file creation (Write to non-existent path) needs no prior Read
        and not (block["name"] == "Write" and not _os.path.exists(path))
    }


# ── Completion-claim grounding (ADR-0031) ────────────────────────────────────
# Detects "ya arreglé X" / "fixed X" style claims and cross-checks them against
# actual Edit/Write/MultiEdit activity. Two signals, strong preferred over weak:
#   1. Strong: a delimited ```task-completion JSON block (model self-report).
#   2. Weak: regex fallback over free text, only when no block is present.

_TASK_COMPLETION_BLOCK_RE = re.compile(r'```task-completion\s*\n(.*?)\n```', re.DOTALL)

_COMPLETION_VERB_RE = re.compile(
    r'\b(fixed|resolved|corrected|completed|closed|arregl\w*|correg\w*|resolv\w*|'
    r'cerr\w*|complet\w*)\b',
    re.IGNORECASE,
)
_BARE_FILE_PATH_RE = re.compile(r'\b([\w][\w/-]*\.\w{1,10})\b')
# Only split on sentence-ending punctuation followed by an uppercase letter (incl.
# common Spanish accented capitals) — a bare "." followed by whitespace+lowercase
# is almost always a file extension continuing mid-sentence ("use-toast.ts y
# también..."), not a real sentence boundary. Avoids severing verb from path.
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ])')


def _extract_completion_report(content: list) -> dict | None:
    """Strong signal: find, parse, and STRIP the delimited task-completion block.

    Mutates the matching text block's "text" field in place to remove it — this
    is proxy-internal bookkeeping and must never reach the user. Returns the
    parsed {"completed": bool, "files_modified": [...]} dict, or None if no
    valid block is present.
    """
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text", "")
        match = _TASK_COMPLETION_BLOCK_RE.search(text)
        if not match:
            continue
        block["text"] = text[: match.start()] + text[match.end():]
        try:
            report = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            logger.warning("[grounding] task-completion block present but invalid JSON")
            return None
        return report if isinstance(report, dict) else None
    return None


def _extract_completion_claims(text: str) -> list[tuple[str, str]]:
    """Weak-signal fallback: sentences with a completion verb + a file mention.

    Only meant to run when no structured task-completion block is present —
    free-text pattern matching is inherently more fragile than a fixed delimited
    format. Returns [(claim_text, file_path), ...].
    """
    claims = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not _COMPLETION_VERB_RE.search(sentence):
            continue
        # A single claim sentence may mention several files ("cerré A y B") —
        # capture all of them, not just the first (search() would miss the rest).
        for path_match in _BARE_FILE_PATH_RE.finditer(sentence):
            claims.append((sentence.strip(), path_match.group(1)))
    return claims


def _extract_all_edited_paths(messages: list) -> set[str]:
    """Return every Edit/Write/MultiEdit target path across the WHOLE conversation.

    Generalizes _extract_edit_paths (single response) to full history — a
    completion claim may reference an edit from an earlier turn (e.g. a final
    summary turn), not just the current response. See ADR-0031.
    """
    paths: set[str] = set()
    for msg in messages or []:
        if bget(msg, "role") != "assistant":
            continue
        content = bget(msg, "content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if bget(block, "type") != "tool_use":
                continue
            if bget(block, "name") not in _WRITE_TOOL_NAMES:
                continue
            path = (bget(block, "input") or {}).get("file_path", "")
            if path:
                paths.add(path)
    return paths


def _path_matches_any(claimed_path: str, edited_paths: set) -> bool:
    """True if claimed_path exactly matches or is a path-suffix of any edited path.

    Handles claims using a short/relative mention ("use-toast.ts") against edits
    that used a fuller path ("hooks/use-toast.ts").
    """
    if claimed_path in edited_paths:
        return True
    return any(
        edited == claimed_path or edited.endswith("/" + claimed_path)
        for edited in edited_paths
    )


# ── Generality-claim grounding (ADR-0033) ────────────────────────────────────
# Detects "todos los callers respetan X" / "every consumer does Y" style claims and
# cross-checks them against Grep/Glob/Bash-grep evidence anywhere in the conversation.
# Weaker signal than completion-claim grounding by design: it can only prove that NO
# search happened, never that a search was sufficient — see ADR-0033's rationale.

_GENERALITY_MARKER_RE = re.compile(
    r'\b(todos?|todas?|casi\s+todos?|siempre|en\s+la\s+pr[aá]ctica|cada\s+vez|'
    r'every|all|always|everywhere|virtually\s+all)\b',
    re.IGNORECASE,
)
_USAGE_SCOPE_RE = re.compile(
    r'\b(caller[s]?|consumer[s]?|consumidor(?:es)?|llamador(?:es)?|uso[s]?|'
    r'usage[s]?|invocaci[oó]n(?:es)?|call\s*site[s]?|codebase|c[oó]digo\s+entero)\b',
    re.IGNORECASE,
)

_SEARCH_TOOL_NAMES = frozenset({"Grep", "Glob"})
_BASH_GREP_RE = re.compile(r'\b(grep|rg|ag)\b')


def _extract_generality_claims(text: str) -> list[str]:
    """Sentences asserting universal/generality behavior about callers/usages.

    Requires BOTH a generality marker (todos/siempre/every/all/...) AND a usage-scope
    noun (caller/consumer/uso/...) in the SAME sentence — mirrors
    _extract_completion_claims's verb+path co-occurrence design, to avoid false
    positives like "arreglé todos los tests" (ADR-0031's territory, not this one).
    """
    return [
        sentence.strip()
        for sentence in _SENTENCE_SPLIT_RE.split(text)
        if _GENERALITY_MARKER_RE.search(sentence) and _USAGE_SCOPE_RE.search(sentence)
    ]


def _has_search_tool_evidence(messages: list) -> bool:
    """True if a Grep/Glob tool_use, or a Bash tool_use running grep/rg/ag, appears
    ANYWHERE in the conversation.

    Conversation-wide, not per-claim — search evidence isn't tied to any single
    claim. Includes Bash-grep because agents frequently search via shell rather than
    the dedicated Grep/Glob tool; without this branch, false positives would be
    constant.
    """
    for msg in messages or []:
        if bget(msg, "role") != "assistant":
            continue
        content = bget(msg, "content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if bget(block, "type") != "tool_use":
                continue
            name = bget(block, "name")
            if name in _SEARCH_TOOL_NAMES:
                return True
            if name == "Bash":
                command = (bget(block, "input") or {}).get("command", "")
                if _BASH_GREP_RE.search(command):
                    return True
    return False


# Claim pattern: sentence with citation
# Matches: "The function X does Y (file.py:123)" - claims with citations
# Skips: "I will..." "Let me..." - planning statements without claims
_CLAIM_PATTERN = re.compile(
    r'(?:[A-Z][^.!?]+(?:does|is|are|has|uses|handles|manages|implements|provides|calls|invokes|returns|throws)[^.!?]+)[.!?]',
    re.IGNORECASE
)


async def _persist_evidence_graph(
    session_id: str,
    evidence_graph: dict,
    code_snippet_cache: dict,
) -> None:
    """Fire-and-forget: persist evidence graph entries to session cache."""
    try:
        from llm.compressor import extend_session_grounding_graph
        await extend_session_grounding_graph(session_id, evidence_graph, code_snippet_cache)
    except Exception as exc:
        logger.warning("[grounding] Evidence graph persistence failed: %s", exc)


class GroundingValidatorTransformer(Transformer):
    """Validate citations against actual tool results and inject code snippets."""

    @property
    def name(self) -> str:
        return "grounding_validator"

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    async def transform(self, request: Any, ctx: TransformContext) -> None:
        """
        Validate citations in model response and build evidence cache.

        1. Extract citations from response text
        2. Cross-reference with tool results in conversation
        3. Extract code snippets from tool results
        4. Set ctx.grounding_score and ctx.grounding_issues
        5. Build multi-hop evidence graph
        """
        if not self.enabled:
            return

        # Get conversation messages (needed for evidence_map and pre-edit check)
        messages = getattr(request, "messages", [])
        if not messages:
            logger.debug("[grounding] No messages in request")
            return

        # Step 2: Get evidence from tool results (needed before Step 2.5)
        evidence_map = self._build_evidence_map(messages)
        logger.info("[grounding] Found evidence for %d unique files", len(evidence_map))

        # Priority 4: augment with historically-read files that survived compression
        if ctx.session_id:
            try:
                from llm.compressor import get_session_read_files
                historical_files = await get_session_read_files(ctx.session_id)
                if historical_files:
                    added = 0
                    for f in historical_files:
                        if f not in evidence_map:
                            evidence_map[f] = []
                            added += 1
                    if added:
                        logger.info("[grounding] Restored %d historical files from session cache", added)
            except Exception as exc:
                logger.warning("[grounding] Failed to load session read files: %s", exc)

        # Step 2.5: Pre-edit read check — runs even for tool-only responses
        # response_content is at request.content (request IS the response in this pipeline)
        unread_edits = _extract_edit_paths(getattr(request, "content", None) or []) - set(evidence_map)
        if unread_edits:
            ensure_system_note(
                ctx,
                f"[grounding-warn] Editing without prior Read: {', '.join(sorted(unread_edits))}\n"
                "Read the file first — editing unread files risks replacing real code with hallucinated content.",
            )
            ctx.unread_edit_files = list(unread_edits)
            logger.warning("[grounding] Unread edit targets: %s", unread_edits)

        # Get response text (citation validation requires text — tool-only responses skip below)
        response_text = self._extract_response_text(request)
        if not response_text:
            logger.debug("[grounding] No response text to validate (tool-only response)")
            return

        # ADR-0031: strong signal — parse + strip the task-completion block (if
        # present) before any further text-based extraction, so its JSON content
        # is never mistaken for prose citations/claims below.
        completion_report = _extract_completion_report(getattr(request, "content", None) or [])
        if completion_report is not None:
            response_text = self._extract_response_text(request)  # re-derive: block stripped in place

        # Step 1: Extract citations from response
        citations = self._extract_citations(response_text)
        logger.info("[grounding] Found %d citations in response", len(citations))

        # Step 3: Extract code snippets from tool results
        code_snippets = self._extract_code_snippets(messages)
        ctx.code_snippet_cache = code_snippets
        logger.info("[grounding] Extracted %d code snippets", len(code_snippets))

        # Step 4: Validate each citation
        validated_citations = []
        invalid_citations = []
        for citation in citations:
            file_path = _FILE_FROM_CITATION.match(citation)
            if not file_path:
                continue
            file_path = file_path.group(1)

            if file_path in evidence_map:
                validated_citations.append(citation)
                # Link citation to code snippet
                snippet = code_snippets.get(file_path, "")
                ctx.evidence_links[citation] = [file_path, snippet]
            else:
                invalid_citations.append(citation)
                ctx.grounding_issues.append(
                    f"Citation '{citation}' points to unread file"
                )

        # Step 5: Calculate grounding score
        if citations:
            ctx.grounding_score = len(validated_citations) / len(citations)
        else:
            # No citations → low grounding score (claims without evidence)
            ctx.grounding_score = 0.0
            ctx.grounding_issues.append("No citations found - claims lack evidence")

        # Step 6: Build citation map
        ctx.citation_map = {c: _FILE_FROM_CITATION.match(c).group(1) for c in validated_citations}

        # Step 7: Build multi-hop evidence graph
        self._build_evidence_graph(ctx, messages, citations)

        # Step 8: Completion-claim verification (ADR-0031)
        # Strong signal (task-completion block) takes precedence; the weak regex
        # fallback only runs when no block was present, to avoid double-counting
        # the same claim under both signals.
        completion_entries: list[tuple[str, str, str]] = []
        if completion_report is not None:
            if completion_report.get("completed"):
                for fp in completion_report.get("files_modified", []) or []:
                    if isinstance(fp, str) and fp:
                        completion_entries.append((f"task-completion: {fp}", fp, "strong"))
        else:
            for claim_text, file_path in _extract_completion_claims(response_text):
                completion_entries.append((claim_text, file_path, "weak"))

        if completion_entries:
            edited_paths = _extract_all_edited_paths(messages)
            for claim_text, file_path, signal in completion_entries:
                verified = _path_matches_any(file_path, edited_paths)
                if not verified:
                    ctx.unverified_completion_claims.append({
                        "claim_text": claim_text[:200],
                        "file_path": file_path,
                        "signal": signal,
                    })
                    ctx.grounding_issues.append(
                        f"Completion claim ({signal}) references '{file_path}' but no "
                        "Edit/Write/MultiEdit targeted that file in this conversation."
                    )
                    ensure_system_note(
                        ctx,
                        f"[grounding-warn] Unverified completion claim ({signal}): you "
                        f"referenced '{file_path}' as fixed/completed, but no "
                        "Edit/Write/MultiEdit touched that file in this conversation. "
                        "If it's actually done, verify with Read/grep; if not, do it "
                        "now before claiming completion.",
                    )
                if ctx.session_id:
                    try:
                        from llm.compressor import append_session_completion_claim
                        asyncio.create_task(
                            append_session_completion_claim(
                                ctx.session_id, claim_text[:200], file_path, verified, signal,
                            )
                        )
                    except Exception as exc:
                        logger.warning("[grounding] Failed to schedule completion-claim persistence: %s", exc)

        # Step 9: Generality-claim verification (ADR-0033)
        # One evidence check per response (not per-claim) — search evidence isn't
        # tied to any single claim, it's conversation-wide.
        generality_claims = _extract_generality_claims(response_text)
        if generality_claims:
            has_search_evidence = _has_search_tool_evidence(messages)
            if not has_search_evidence:
                for claim_text in generality_claims:
                    ctx.unverified_generality_claims.append({
                        "claim_text": claim_text[:200],
                        "signal": "weak",
                    })
                ctx.grounding_issues.append(
                    "Generality claim without any Grep/Glob/grep evidence in this "
                    f"conversation: '{generality_claims[0][:100]}...'"
                )
                ensure_system_note(
                    ctx,
                    "[grounding-warn] Unverified generality claim: you asserted "
                    "something is true for 'all/every/always' callers/usages without "
                    "any Grep/Glob search in this conversation. This only confirms NO "
                    "search happened — if you already searched, this is a false "
                    "positive; if you haven't, verify before asserting broadly.",
                )
            if ctx.session_id:
                for claim_text in generality_claims:
                    try:
                        from llm.compressor import append_session_generality_claim
                        asyncio.create_task(
                            append_session_generality_claim(
                                ctx.session_id, claim_text[:200], has_search_evidence, "weak",
                            )
                        )
                    except Exception as exc:
                        logger.warning("[grounding] Failed to schedule generality-claim persistence: %s", exc)

        # Priority 4: flag stale evidence entries (>30min without verification)
        _stale_threshold_secs = 1800
        _now = time.time()
        for entity, data in ctx.evidence_graph.items():
            last_verified = data.get("last_verified", _now)
            age_secs = _now - last_verified
            age_minutes = int(age_secs / 60)
            if age_secs > _stale_threshold_secs:
                ctx.grounding_issues.append(
                    f"Evidence for '{entity}' stale ({age_minutes}m since last verification)"
                )

        # Priority 4: persist new evidence back to session cache (fire-and-forget)
        if ctx.session_id and ctx.evidence_graph:
            try:
                asyncio.create_task(
                    _persist_evidence_graph(ctx.session_id, ctx.evidence_graph, ctx.code_snippet_cache)
                )
            except Exception as exc:
                logger.warning("[grounding] Failed to schedule evidence persistence: %s", exc)

        if citations:
            logger.info(
                "[grounding] Validated: %d/%d citations (%.0f%%)",
                len(validated_citations), len(citations), ctx.grounding_score * 100
            )
            if validated_citations:
                logger.info("[grounding] Validated: %s", validated_citations[:5])
            if invalid_citations:
                logger.warning("[grounding] Invalid: %s", invalid_citations)
        else:
            logger.debug("[grounding] No citations found (text_len=%d)", len(response_text))

    def _extract_response_text(self, request: Any) -> str:
        """Extract text content from response."""
        text_parts = []
        content = getattr(request, "content", [])

        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

        return "\n".join(text_parts)

    def _extract_citations(self, text: str) -> list[str]:
        """Extract (file.py:123) style citations from text."""
        return _CITATION_PATTERN.findall(text)

    def _build_evidence_map(self, messages: list) -> dict[str, list[str]]:
        """
        Build map of file → evidence from tool results.

        Scans conversation for Read tool results and maps file paths.
        """
        evidence = {}

        for msg in messages:
            if bget(msg, "role") != "assistant":
                continue
            content = bget(msg, "content") or []

            for block in content:
                if bget(block, "type") != "tool_use":
                    continue
                if bget(block, "name") != "Read":
                    continue

                tool_input = bget(block, "input") or {}
                file_path = tool_input.get("file_path", "")
                if not file_path:
                    continue

                # Track that this file was read
                if file_path not in evidence:
                    evidence[file_path] = []

        return evidence

    def _extract_code_snippets(self, messages: list) -> dict[str, str]:
        """
        Extract code snippets from tool_result messages.

        Returns: {file_path: code_snippet} mapping
        Code snippet is first 500 characters of the tool_result content.
        """
        snippets = {}

        for msg in messages:
            if bget(msg, "role") != "user":
                continue
            content = bget(msg, "content") or []

            for block in content:
                if bget(block, "type") != "tool_result":
                    continue

                # Find the corresponding tool_use to get file path
                tool_use_id = bget(block, "tool_use_id") or ""
                file_path = self._find_file_path_for_tool_id(messages, tool_use_id)
                if not file_path:
                    continue

                # Extract code snippet from tool_result content
                result_content = bget(block, "content") or ""
                if isinstance(result_content, dict):
                    result_content = result_content.get("text", str(result_content))
                elif isinstance(result_content, list):
                    result_content = "\n".join(str(x) for x in result_content)

                # Store first 500 chars as snippet
                snippet = str(result_content)[:500]
                if file_path not in snippets:
                    snippets[file_path] = snippet

        return snippets

    def _find_file_path_for_tool_id(self, messages: list, tool_use_id: str) -> str | None:
        """Find file path for a given tool_use_id."""
        for msg in messages:
            if bget(msg, "role") != "assistant":
                continue
            content = bget(msg, "content") or []

            for block in content:
                if bget(block, "type") != "tool_use":
                    continue
                if bget(block, "id") != tool_use_id:
                    continue
                if bget(block, "name") != "Read":
                    continue

                tool_input = bget(block, "input") or {}
                return tool_input.get("file_path", "")
        return None

    def _build_evidence_graph(
        self,
        ctx: TransformContext,
        messages: list,
        citations: list[str]
    ) -> None:
        """
        Build multi-hop evidence graph from conversation.

        Tracks entity relationships across tool results.
        Example: AuthService → validateToken() → error_handler.py
        """
        for citation in citations:
            file_path = _FILE_FROM_CITATION.match(citation)
            if not file_path:
                continue
            file_path = file_path.group(1)

            # Extract entities from file path (class/function names)
            entities = self._extract_entities_from_file(file_path)

            # Add to evidence graph with temporal metadata
            now = time.time()
            for entity in entities:
                if entity not in ctx.evidence_graph:
                    ctx.evidence_graph[entity] = {
                        "file": file_path,
                        "related": [],
                        "citations": [],
                        "code_snippet": ctx.code_snippet_cache.get(file_path, ""),
                        "first_seen": now,
                        "last_verified": now,
                    }
                else:
                    ctx.evidence_graph[entity]["last_verified"] = now
                ctx.evidence_graph[entity]["citations"].append(citation)

        # Link related entities (simple heuristic: same directory)
        files_by_dir = {}
        for file_path in ctx.code_snippet_cache.keys():
            dir_name = str(file_path).rsplit("/", 1)[0]
            files_by_dir.setdefault(dir_name, []).append(file_path)

        # Link files in same directory as related
        for entity, data in ctx.evidence_graph.items():
            dir_name = data["file"].rsplit("/", 1)[0]
            related_files = files_by_dir.get(dir_name, [])
            for related_file in related_files:
                if related_file != data["file"]:
                    data["related"].append(related_file)

    def _extract_entities_from_file(self, file_path: str) -> list[str]:
        """Extract entity names from file path (simple heuristic)."""
        # Extract filename without extension
        filename = str(file_path).rsplit("/", 1)[-1]
        name = filename.rsplit(".", 1)[0]
        # Convert snake_case to CamelCase for class names
        class_name = "".join(word.capitalize() for word in name.split("_"))
        return [class_name, name]