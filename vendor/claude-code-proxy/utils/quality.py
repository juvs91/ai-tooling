# utils/quality.py — Response quality heuristics for per-model comparison.
from __future__ import annotations

import json
import re

# Pre-compiled regexes for analysis heuristics
_GENERIC_PHRASES = re.compile(
    r"\b(handles|manages|processes|deals with|is responsible for|takes care of)\b",
    re.IGNORECASE,
)
_FILE_MENTION_RE = re.compile(r"[\w/\-]+\.(?:py|ts|js|tsx|jsx|go|rs|java|rb)\b")
_CONCRETE_NUMBER_RE = re.compile(r"\b\d{2,}\b")  # numbers >= 10
_FACTUAL_CLAIM_RE = re.compile(
    r"(?:has|tiene|contains|contiene|takes|toma|runs|ejecuta|handles|returns)\s+"
    r"\d+\s+(?:line|línea|class|clase|function|función|parameter|parámetro|step|paso|"
    r"module|módulo|file|archivo|endpoint|transformer|heuristic)",
    re.IGNORECASE,
)
_READ_TOOL_NAMES = frozenset({"Read", "Glob", "Grep", "Agent", "WebFetch", "WebSearch"})

# H_SPECIFICITY: code-level references that distinguish deep analysis from generic text
_LINE_REF_RE = re.compile(r"(?:line\s+\d+|:\d{2,4}\b|\bL\d{2,}\b|línea\s+\d+)")
_FUNC_REF_RE = re.compile(r"\b\w{3,}\(\)|\bdef \w+\b|\bclass \w+\b")
_FILE_PATH_RE = re.compile(r"\b[\w/\-]+\.py(?::\d+)?\b")


def score_response(
    intent: str,
    response_text: str,
    tool_calls: list[dict] | None = None,
    is_analysis: bool = False,
    phase: str = "",
    model_used: str = "",
    input_tokens: int = 0,
) -> tuple[float, list[str]]:
    """Score a model response on quality heuristics.

    Returns (score 0.0–1.0, list of issue strings).
    Applies general heuristics (H1-H5) to all responses, plus
    analysis-specific heuristics (H6-H9, H11b, H_SPECIFICITY) when is_analysis=True.

    input_tokens: approximate input context size. When > 15000 the analysis is
    assumed to come from context (passthrough GLM-4.7), so tool-centric heuristics
    H6/H7 are skipped — the model already has the data, it doesn't need to read it
    during the response.
    """
    score = 1.0
    issues: list[str] = []
    tool_calls = tool_calls or []
    text_len = len(response_text)

    # ── General heuristics (all intents) ──────────────────────────────

    # H1: Response length vs intent expectations (applies to PLANNING and analysis)
    min_planning_len = 500 if is_analysis else 200
    if (intent in ("PLAN", "PLANNING") or is_analysis) and text_len < min_planning_len and not tool_calls:
        issues.append("planning_too_short")
        score -= 0.3

    # H2: Tool argument validity (JSON parseable)
    for tc in tool_calls:
        inp = tc.get("input")
        if inp is None:
            continue
        if isinstance(inp, str):
            try:
                json.loads(inp)
            except (json.JSONDecodeError, ValueError):
                issues.append("invalid_tool_json")
                score -= 0.2
                break  # one penalty is enough

    # H3: Reasoning leak detection
    if "<reasoning>" in response_text:
        issues.append("reasoning_leak")
        score -= 0.2

    # H4: Empty response
    if not response_text.strip() and not tool_calls:
        issues.append("empty_response")
        score -= 0.5

    # H5: Unclosed code blocks (odd number of ```)
    if response_text.count("```") % 2 != 0:
        issues.append("truncated_code_block")
        score -= 0.15

    # H10: Chat verbosity
    if intent == "CHAT" and text_len > 8000:
        issues.append("chat_too_verbose")
        score -= 0.1

    # ── Analysis-specific heuristics (H6-H12) ────────────────────────

    # context_inclusive: analysis where data is already in context (passthrough GLM-4.7).
    # In this mode, 0 tool calls during response is CORRECT — the model read files
    # in prior turns and they're in the context window. Tool-centric heuristics
    # H6/H7 would false-positive on every valid response.
    context_inclusive = is_analysis and input_tokens > 15000

    if is_analysis:
        # H11: Analysis output too shallow — short text AND no tools at all
        # Only penalize truly empty responses; tool-only responses are normal CC
        # behavior (model gathers data via Read/Glob/Bash before answering).
        if text_len < 300 and len(tool_calls) == 0:
            issues.append("analysis_too_shallow")
            score -= 0.4

        # H11b: Shallow output ratio — for large-context analysis the output must
        # be proportional to the input complexity. A 60K-token context that produces
        # 200 chars is definitively superficial regardless of tools.
        if context_inclusive and len(tool_calls) == 0:
            # Minimum expected: ~1 char per 20 input tokens, capped at 3000
            expected_min = min(3000, input_tokens // 20)
            if text_len < expected_min // 3:        # < 33% of expected
                issues.append("critically_shallow_output")
                score -= 0.35
            elif text_len < expected_min // 1.5:    # < 67% of expected
                issues.append("shallow_output")
                score -= 0.15

        # H12: REMOVED — tool-only responses are normal Claude Code behavior.
        # A response with tools IS substance: the model is gathering data.
        # Penalizing this caused false refinements that duplicated requests.

    if is_analysis and response_text.strip():
        # Count file mentions and read-type tool calls
        file_mentions = _FILE_MENTION_RE.findall(response_text)
        read_tool_count = sum(
            1 for tc in tool_calls
            if tc.get("name") in _READ_TOOL_NAMES
        )

        # H6: Shallow exploration — mentions files but didn't read them.
        # SKIPPED for context-inclusive analysis: reading happened in prior turns.
        if not context_inclusive:
            if len(file_mentions) > 3 and read_tool_count == 0:
                issues.append("shallow_exploration")
                score -= 0.25
            elif len(file_mentions) > 3:
                ratio = read_tool_count / len(file_mentions)
                if ratio < 0.3:
                    issues.append("shallow_exploration")
                    score -= 0.25

        # H7: Unverified claims — factual statements without tool usage.
        # SKIPPED for context-inclusive analysis: claims are backed by context data.
        # SKIPPED for SYNTHESIZING intent: model has no tools by design (deepseek-reasoner),
        # all claims come from the prior READ phase already gathered in context.
        if not context_inclusive and intent != "SYNTHESIZING":
            factual_claims = _FACTUAL_CLAIM_RE.findall(response_text)
            if len(factual_claims) > 5 and not tool_calls:
                issues.append("unverified_claims")
                score -= 0.3

        # H8: Too generic — superficial analysis full of vague phrases.
        # Increased penalty for analysis (-0.25 vs -0.15 for other intents).
        generic_count = len(_GENERIC_PHRASES.findall(response_text))
        if generic_count > 3:
            penalty = -0.25 if is_analysis else -0.15
            issues.append(f"too_generic({generic_count}_matches)")
            score += penalty

        # H9: Lacks specificity — long text without concrete numbers.
        # Tightened: threshold lowered to 5, penalty increased to -0.15.
        concrete_numbers = _CONCRETE_NUMBER_RE.findall(response_text)
        if text_len > 1000 and len(concrete_numbers) < 5:
            issues.append("lacks_specificity")
            score -= 0.15

        # H_SPECIFICITY: code-level references distinguish deep analysis from
        # generic summaries. Good analysis cites line numbers, function calls, file
        # paths. A 1000-char response with 0 code references is generic by definition.
        if text_len > 500:
            line_refs = len(_LINE_REF_RE.findall(response_text))
            func_refs = len(_FUNC_REF_RE.findall(response_text))
            file_refs = len(_FILE_PATH_RE.findall(response_text))
            specificity_score = line_refs * 2 + func_refs + file_refs
            if specificity_score < 5:
                issues.append("lacks_code_specificity")
                score -= 0.20
            elif specificity_score < 10:
                issues.append("low_code_specificity")
                score -= 0.10

    # ── Agent progress heuristic (H16) ──────────────────────────────

    # H16: Detect cyclical reads — if agent is only reading the same files
    # repeatedly without making progress (writing, editing), penalize.
    if tool_calls:
        read_paths: list[str] = []
        write_count = 0
        for tc in tool_calls:
            tc_name = tc.get("name", "")
            inp = tc.get("input") or {}
            if isinstance(inp, dict):
                if tc_name in ("Read", "Glob", "Grep"):
                    path = inp.get("file_path", "") or inp.get("path", "") or inp.get("pattern", "")
                    if path:
                        read_paths.append(path)
                elif tc_name in ("Write", "Edit", "NotebookEdit"):
                    write_count += 1
        # Detect duplicate reads (same path multiple times in one response)
        if read_paths:
            unique_reads = set(read_paths)
            if len(read_paths) > 3 and len(unique_reads) <= 1:
                issues.append("cyclical_reads")
                score -= 0.3

    # ── Routing mismatch heuristic (H15) ─────────────────────────────

    # H15: Routing mismatch — planning intent on explore-tier model
    if phase == "EXPLORE" and intent in ("PLAN", "PLANNING") and text_len > 500:
        issues.append("routing_mismatch(planning_to_explore)")
        score -= 0.25
    # H15b: Building intent on explore-tier model with tool calls
    elif phase == "EXPLORE" and intent in ("BUILD", "BUILDING") and tool_calls:
        issues.append("routing_mismatch(building_to_explore)")
        score -= 0.2

    # ── Tool quality heuristics (H13-H14, all intents) ───────────────

    # H13: Tool name hallucination — tool name not in the standard CC set
    _CC_TOOL_NAMES = {
        "Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch",
        "Agent", "TodoWrite", "NotebookEdit", "AskUserQuestion",
    }
    for tc in tool_calls:
        tc_name = tc.get("name", "")
        if tc_name and tc_name not in _CC_TOOL_NAMES:
            # Allow MCP tool names (contain double underscores like mcp__server__tool)
            if "__" not in tc_name:
                issues.append(f"hallucinated_tool({tc_name})")
                score -= 0.15
                break  # one penalty is enough

    # H14: Tool args with empty required parameters
    _REQUIRED_PARAMS = {
        "Read": {"file_path"}, "Write": {"file_path", "content"},
        "Edit": {"file_path", "old_string", "new_string"},
        "Bash": {"command"}, "Glob": {"pattern"}, "Grep": {"pattern"},
    }
    for tc in tool_calls:
        tc_name = tc.get("name", "")
        required = _REQUIRED_PARAMS.get(tc_name)
        if not required:
            continue
        inp = tc.get("input") or {}
        if isinstance(inp, dict):
            empty_required = [k for k in required if k in inp and inp[k] == ""]
            if empty_required:
                issues.append(f"empty_required_args({tc_name}:{','.join(empty_required)})")
                score -= 0.2
                break

    # ── Speculative generation heuristic (H17) ───────────────────────

    # H17: Detect speculative generation - analysis without evidence
    if is_analysis and response_text.strip():
        h17_result = _detect_speculative_generation(response_text, tool_calls)
        if h17_result:
            issue, penalty = h17_result
            issues.append(issue)
            score += penalty

    return max(0.0, round(score, 2)), issues


def _detect_speculative_generation(response_text: str, tool_calls: list) -> tuple[str, float] | None:
    """
    Detect speculative generation - analysis written without evidence of verification.

    This is GENERIC - works for any language/project because it measures
    EVIDENCE PATTERNS, not specific file extensions.

    Returns (issue_name, penalty) or None.
    """
    # 1. Code citations without verification tools
    code_citation_patterns = [
        r'\.(py|ts|js|go|rs|java|cpp|c|js|ts):\d+',   # file:line
        r'(def |class |function |interface )\w+\(',      # code signatures
        r'line \d+ (shows|has|contains)',                # line references
    ]

    has_code_citations = any(
        re.search(pattern, response_text)
        for pattern in code_citation_patterns
    )

    has_read_tools = any(
        tc.get("name") in {"Read", "Grep", "Glob"}
        for tc in tool_calls
    )

    # Code cited without tools → speculative
    if has_code_citations and not has_read_tools:
        return "code_citations_without_verification", -0.35

    # 2. Long analysis with tools but no evidence phrases
    if len(response_text) > 1500 and has_read_tools:
        evidence_phrases = [
            r"(?:i )?(?:read|found|saw|discovered|noticed|observed)\s+(?:in|that|the)",
            r"(?:according|based)\s+(?:to|on)",
            r"(?:file|path|(?:server|client|utils|compressor|streaming)\.py)\s+(?:shows?|has?|contains?)",
            r"(?:grep|glob|search)\s+(?:found|returned|showed)",
            r"from\s+[\w.]+\s+(?:we\s+)?(?:can\s+)?see",
        ]

        has_evidence = any(
            re.search(pattern, response_text, re.IGNORECASE)
            for pattern in evidence_phrases
        )

        if not has_evidence:
            # Has tool calls but doesn't cite them → decorative
            return "decorative_tool_calls_no_evidence", -0.30

    # 3. Bug/issue claims without discovery verbs
    bug_claim_patterns = [
        r'(?:bug|issue|problem|error|leak|race condition|vulnerability|critical)',
    ]

    discovery_verbs = [
        r'(?:discovered|found|noticed|observed|identified|detected)',
    ]

    has_bug_claims = any(
        re.search(pattern, response_text, re.IGNORECASE)
        for pattern in bug_claim_patterns
    )

    has_discovery_verbs = any(
        re.search(pattern, response_text, re.IGNORECASE)
        for pattern in discovery_verbs
    )

    if has_bug_claims and not has_discovery_verbs and len(response_text) > 500:
        # Claims bugs but no evidence of how they were found
        return "unverified_bug_claims", -0.25

    return None
