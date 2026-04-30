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
import logging
import re
import time
from typing import Any

from llm.pipeline import Transformer, TransformContext
from utils.utils import bget

logger = logging.getLogger(__name__)

# Citation pattern: (file.py:123) or [file.py:123] or file.py:123
_CITATION_PATTERN = re.compile(r'[\[\(]([\w/.-]+\.\w+:\d+)[\)\]]')
# Extract file path from citation
_FILE_FROM_CITATION = re.compile(r'([\w/.-]+\.\w+):\d+')

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

        # Get response text
        response_text = self._extract_response_text(request)
        if not response_text:
            logger.debug("[grounding] No response text to validate")
            return

        # Get conversation messages (for tool results)
        messages = getattr(request, "messages", [])
        if not messages:
            logger.debug("[grounding] No messages in request")
            return

        # Step 1: Extract citations from response
        citations = self._extract_citations(response_text)
        logger.info("[grounding] Found %d citations in response", len(citations))

        # Step 2: Get evidence from tool results
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