"""
Unit tests for GroundingValidatorTransformer.

Tests citation extraction, evidence validation, code snippet extraction,
and multi-hop evidence graph building.
"""
import pytest
from llm.pipeline import TransformContext
from llm.transformers.grounding_validator import GroundingValidatorTransformer


class MockRequest:
    """Mock request object for testing."""

    def __init__(self, content: list, messages: list):
        self.content = content
        self.messages = messages


@pytest.mark.asyncio
async def test_extract_citations():
    """Test citation extraction from response text."""
    transformer = GroundingValidatorTransformer()
    text = "The function foo() does bar (module.py:42) and baz (other.py:123)"
    citations = transformer._extract_citations(text)
    assert len(citations) == 2
    assert "module.py:42" in citations
    assert "other.py:123" in citations


@pytest.mark.asyncio
async def test_extract_citations_brackets():
    """Test citation extraction with bracket notation."""
    transformer = GroundingValidatorTransformer()
    text = "The function foo() does bar [module.py:42] and baz (other.py:123)"
    citations = transformer._extract_citations(text)
    assert len(citations) == 2
    assert "module.py:42" in citations
    assert "other.py:123" in citations


@pytest.mark.asyncio
async def test_build_evidence_map():
    """Test building evidence map from tool results."""
    transformer = GroundingValidatorTransformer()
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "module.py"}}
            ]
        }
    ]
    evidence = transformer._build_evidence_map(messages)
    assert "module.py" in evidence


@pytest.mark.asyncio
async def test_extract_code_snippets():
    """Test extracting code snippets from tool results."""
    transformer = GroundingValidatorTransformer()
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Read", "id": "tool_1", "input": {"file_path": "module.py"}}
            ]
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool_1", "content": "def foo():\n    return 'bar'"}
            ]
        }
    ]
    snippets = transformer._extract_code_snippets(messages)
    assert "module.py" in snippets
    assert "def foo" in snippets["module.py"]


@pytest.mark.asyncio
async def test_validate_citations():
    """Test full citation validation."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()

    request = MockRequest(
        content=[{"type": "text", "text": "Function foo() does bar (module.py:42)"}],
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "id": "tool_1", "input": {"file_path": "module.py"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": "def foo():\n    return 'bar'"}
                ]
            }
        ]
    )

    await transformer.transform(request, ctx)

    # Should have validated the citation
    assert ctx.grounding_score > 0
    assert len(ctx.citation_map) > 0
    assert "module.py" in ctx.code_snippet_cache


@pytest.mark.asyncio
async def test_invalid_citation():
    """Test rejection of citations for unread files."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()

    request = MockRequest(
        content=[{"type": "text", "text": "Function foo() does bar (module.py:42)"}],
        messages=[
            # Read a different file, but not module.py
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "id": "tool_1", "input": {"file_path": "other.py"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": "def other():\n    return 'other'"}
                ]
            }
        ]
    )

    await transformer.transform(request, ctx)

    # Should have low grounding score (citation to unread file)
    assert ctx.grounding_score < 1.0
    assert len(ctx.grounding_issues) > 0
    assert "unread file" in ctx.grounding_issues[0].lower()


@pytest.mark.asyncio
async def test_no_citations():
    """Test response without citations gets low score."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()

    request = MockRequest(
        content=[{"type": "text", "text": "Function foo() does bar"}],
        messages=[
            # Some messages exist, but response has no citations
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "id": "tool_1", "input": {"file_path": "module.py"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": "def foo():\n    return 'bar'"}
                ]
            }
        ]
    )

    await transformer.transform(request, ctx)

    # No citations = low grounding score
    assert ctx.grounding_score == 0.0
    assert "no citations" in ctx.grounding_issues[0].lower()


@pytest.mark.asyncio
async def test_evidence_graph_building():
    """Test multi-hop evidence graph building."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()

    request = MockRequest(
        content=[{"type": "text", "text": "Function foo() does bar (module.py:42)"}],
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "id": "tool_1", "input": {"file_path": "module.py"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": "def foo():\n    return 'bar'"}
                ]
            }
        ]
    )

    await transformer.transform(request, ctx)

    # Should have built evidence graph
    assert len(ctx.evidence_graph) > 0
    # Entity extracted from file name
    assert "Module" in ctx.evidence_graph or "module" in ctx.evidence_graph


@pytest.mark.asyncio
async def test_grounding_disabled():
    """Test transformer skips when disabled."""
    transformer = GroundingValidatorTransformer(enabled=False)
    ctx = TransformContext()

    request = MockRequest(
        content=[{"type": "text", "text": "Function foo() does bar"}],
        messages=[]
    )

    await transformer.transform(request, ctx)

    # Should not have run validation
    assert ctx.grounding_score == 1.0  # Default value
    assert len(ctx.grounding_issues) == 0


@pytest.mark.asyncio
async def test_multiple_citations():
    """Test validation of multiple citations in one response."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()

    request = MockRequest(
        content=[{
            "type": "text",
            "text": "The AuthService uses TokenValidator (auth.py:42) and Logger (utils.py:123)"
        }],
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "id": "tool_1", "input": {"file_path": "auth.py"}},
                    {"type": "tool_use", "name": "Read", "id": "tool_2", "input": {"file_path": "utils.py"}},
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": "class AuthService: ..."},
                    {"type": "tool_result", "tool_use_id": "tool_2", "content": "def Logger: ..."},
                ]
            }
        ]
    )

    await transformer.transform(request, ctx)

    # Should have validated both citations
    assert ctx.grounding_score == 1.0  # All citations validated
    assert len(ctx.citation_map) == 2
    assert "auth.py" in ctx.citation_map.values()
    assert "utils.py" in ctx.citation_map.values()


@pytest.mark.asyncio
async def test_mixed_valid_invalid_citations():
    """Test validation with mix of valid and invalid citations."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()

    request = MockRequest(
        content=[{
            "type": "text",
            "text": "The AuthService (auth.py:42) and FakeService (fake.py:99) both exist"
        }],
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "id": "tool_1", "input": {"file_path": "auth.py"}},
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": "class AuthService: ..."},
                ]
            }
        ]
    )

    await transformer.transform(request, ctx)

    # Should have 50% grounding score (1/2 citations valid)
    assert ctx.grounding_score == 0.5
    assert len(ctx.grounding_issues) > 0
    assert "fake.py" in ctx.grounding_issues[0].lower()


@pytest.mark.asyncio
async def test_code_snippet_truncation():
    """Test that code snippets are truncated to 500 chars."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()

    long_code = "def foo():\n    return 'bar' " * 100  # Very long code

    request = MockRequest(
        content=[{"type": "text", "text": "Function foo() (module.py:42)"}],
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "id": "tool_1", "input": {"file_path": "module.py"}}
                ]
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": long_code}
                ]
            }
        ]
    )

    await transformer.transform(request, ctx)

    # Snippet should be truncated to 500 chars
    snippet = ctx.code_snippet_cache.get("module.py", "")
    assert len(snippet) <= 500
    assert snippet.startswith("def foo():")


@pytest.mark.asyncio
async def test_extract_entities_from_file():
    """Test entity extraction from file paths."""
    transformer = GroundingValidatorTransformer()

    # Test snake_case file
    entities = transformer._extract_entities_from_file("auth_service.py")
    assert "AuthService" in entities
    assert "auth_service" in entities

    # Test simple file
    entities = transformer._extract_entities_from_file("module.py")
    assert "Module" in entities
    assert "module" in entities

    # Test nested path
    entities = transformer._extract_entities_from_file("src/utils/token_validator.py")
    assert "TokenValidator" in entities
    assert "token_validator" in entities


# ── Completion-claim grounding (ADR-0031) ────────────────────────────────────

def _edit_message(file_path: str, tool_id: str = "e1") -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "name": "Edit", "id": tool_id,
             "input": {"file_path": file_path, "old_string": "a", "new_string": "b"}}
        ],
    }


@pytest.mark.asyncio
async def test_strong_signal_verified():
    """task-completion block whose files_modified match a real Edit → no issue."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()
    request = MockRequest(
        content=[{
            "type": "text",
            "text": (
                "Ya arreglé todo.\n"
                "```task-completion\n"
                '{"completed": true, "files_modified": ["hooks/use-schedules.ts"]}\n'
                "```\n"
            ),
        }],
        messages=[_edit_message("hooks/use-schedules.ts")],
    )
    await transformer.transform(request, ctx)
    assert ctx.unverified_completion_claims == []
    # The structured block must be stripped from what the user sees.
    assert "task-completion" not in request.content[0]["text"]


@pytest.mark.asyncio
async def test_strong_signal_unverified():
    """task-completion block claiming a file with NO matching Edit → flagged."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()
    request = MockRequest(
        content=[{
            "type": "text",
            "text": (
                "Ya arreglé todo.\n"
                "```task-completion\n"
                '{"completed": true, "files_modified": ["hooks/use-toast.ts"]}\n'
                "```\n"
            ),
        }],
        messages=[_edit_message("hooks/use-schedules.ts")],  # different file
    )
    await transformer.transform(request, ctx)
    assert len(ctx.unverified_completion_claims) == 1
    entry = ctx.unverified_completion_claims[0]
    assert entry["file_path"] == "hooks/use-toast.ts"
    assert entry["signal"] == "strong"
    assert any("Completion claim (strong)" in issue for issue in ctx.grounding_issues)


@pytest.mark.asyncio
async def test_weak_signal_verified():
    """Free-text completion claim backed by a real Edit → no issue."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()
    request = MockRequest(
        content=[{"type": "text", "text": "Ya arreglé el bug en use-schedules.ts, todo listo."}],
        messages=[_edit_message("hooks/use-schedules.ts")],
    )
    await transformer.transform(request, ctx)
    assert ctx.unverified_completion_claims == []


@pytest.mark.asyncio
async def test_weak_signal_unverified_reproduces_kimi_case():
    """Reproduces the real Kimi failure: claims 2 files fixed, only 1 was edited.

    'use-schedules.ts' had a real Edit; 'use-toast.ts' did not (fabricated claim).
    """
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()
    request = MockRequest(
        content=[{
            "type": "text",
            "text": "Ya cerré use-schedules.ts y también fixed use-toast.ts, todo listo.",
        }],
        messages=[_edit_message("hooks/use-schedules.ts")],
    )
    await transformer.transform(request, ctx)
    assert len(ctx.unverified_completion_claims) == 1
    assert ctx.unverified_completion_claims[0]["file_path"] == "use-toast.ts"
    assert ctx.unverified_completion_claims[0]["signal"] == "weak"


@pytest.mark.asyncio
async def test_strong_signal_suppresses_weak_regex_fallback():
    """When a task-completion block is present, the weak regex must NOT also run
    (avoids double-counting the same claim under both signals)."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()
    request = MockRequest(
        content=[{
            "type": "text",
            "text": (
                "Ya arreglé el bug.\n"
                "```task-completion\n"
                '{"completed": true, "files_modified": ["hooks/use-schedules.ts"]}\n'
                "```\n"
            ),
        }],
        messages=[_edit_message("hooks/use-schedules.ts")],
    )
    await transformer.transform(request, ctx)
    # Only the strong-signal entry could ever be produced here; if the weak regex
    # also ran on the pre-strip text it would double up on the same claim.
    assert len(ctx.unverified_completion_claims) == 0


@pytest.mark.asyncio
async def test_suffix_path_matching():
    """A claim mentioning a short path ('use-toast.ts') must match an Edit that
    used a fuller path ('hooks/use-toast.ts')."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext()
    request = MockRequest(
        content=[{"type": "text", "text": "Ya arreglé el bug en use-toast.ts."}],
        messages=[_edit_message("hooks/use-toast.ts")],
    )
    await transformer.transform(request, ctx)
    assert ctx.unverified_completion_claims == []


# ── Generality-claim grounding (ADR-0033) ────────────────────────────────────

def _grep_message(tool_id: str = "g1") -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "name": "Grep", "id": tool_id,
             "input": {"pattern": "useApiData"}}
        ],
    }


def _bash_grep_message(tool_id: str = "b1") -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "name": "Bash", "id": tool_id,
             "input": {"command": "grep -rn 'useApiData' app/"}}
        ],
    }


@pytest.mark.asyncio
async def test_generality_claim_without_search_evidence_flagged():
    """'Todos los callers respetan X' with zero Grep/Glob/Bash-grep in the
    conversation → flagged, and persisted with verified=False."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext(session_id="gen-test-1")
    request = MockRequest(
        content=[{
            "type": "text",
            "text": "En la práctica, todos los callers respetan el contrato del hook.",
        }],
        messages=[{"role": "user", "content": "revisa el hook"}],
    )
    await transformer.transform(request, ctx)
    assert len(ctx.unverified_generality_claims) == 1
    assert ctx.unverified_generality_claims[0]["signal"] == "weak"
    assert any("Generality claim" in issue for issue in ctx.grounding_issues)


@pytest.mark.asyncio
async def test_generality_claim_with_native_grep_evidence_not_flagged():
    """Same claim, but a Grep tool_use exists somewhere in the conversation →
    not flagged (search evidence is conversation-wide, not per-claim)."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext(session_id="gen-test-2")
    request = MockRequest(
        content=[{
            "type": "text",
            "text": "En la práctica, todos los callers respetan el contrato del hook.",
        }],
        messages=[_grep_message()],
    )
    await transformer.transform(request, ctx)
    assert ctx.unverified_generality_claims == []


@pytest.mark.asyncio
async def test_generality_claim_with_bash_grep_evidence_not_flagged():
    """A Bash tool_use running `grep` counts as search evidence too — agents
    frequently search via shell rather than the dedicated Grep/Glob tool."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext(session_id="gen-test-3")
    request = MockRequest(
        content=[{
            "type": "text",
            "text": "Every consumer of this hook always respects the contract.",
        }],
        messages=[_bash_grep_message()],
    )
    await transformer.transform(request, ctx)
    assert ctx.unverified_generality_claims == []


@pytest.mark.asyncio
async def test_no_generality_marker_no_effect():
    """Plain text with no generality/universality marker → no effect at all."""
    transformer = GroundingValidatorTransformer()
    ctx = TransformContext(session_id="gen-test-4")
    request = MockRequest(
        content=[{"type": "text", "text": "Corregí el bug en auth.py."}],
        messages=[{"role": "user", "content": "arregla el bug"}],
    )
    await transformer.transform(request, ctx)
    assert ctx.unverified_generality_claims == []