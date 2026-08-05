# tests/test_exploration_grounding_rule.py
"""Unit tests for llm/state_assertion_rules/exploration_grounding.py (ADR-0040)."""
import pytest

from llm.state_assertion_rules.exploration_grounding import ExplorationGroundingRule


class _Ctx:
    def __init__(self, code_snippet_cache=None):
        self.code_snippet_cache = code_snippet_cache or {}


def _content(text, edits=None):
    blocks = [{"type": "text", "text": text}] if text else []
    for path in (edits or []):
        blocks.append({
            "type": "tool_use", "name": "Edit",
            "input": {"file_path": path},
        })
    return blocks


def _evaluate(content, snippet_cache):
    rule = ExplorationGroundingRule()
    return rule.evaluate(
        content=content, messages=[], ctx=_Ctx(snippet_cache), tools=None,
        model="kimi-k2", session_snapshot={},
    )


class TestNoOverlapFires:
    def test_edit_with_zero_shared_tokens_fires(self):
        content = _content(
            "I updated the payment configuration to add a new discount rule.",
            edits=["sources.py"],
        )
        snippet_cache = {
            "sources.py": "class OrmResolver:\n    def resolve_precedence(self, endpoint, pydantic_model):"
        }
        findings = _evaluate(content, snippet_cache)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "exploration_grounding"
        assert f.subject == "sources.py"
        assert f.verdict == "unverifiable"
        assert f.severity == "nudge"


class TestOverlapDoesNotFire:
    def test_edit_referencing_read_content_does_not_fire(self):
        content = _content(
            "I added resolve_precedence handling to OrmResolver so source "
            "precedence is respected.",
            edits=["sources.py"],
        )
        snippet_cache = {
            "sources.py": "class OrmResolver:\n    def resolve_precedence(self, endpoint, pydantic_model):"
        }
        findings = _evaluate(content, snippet_cache)
        assert findings == []

    def test_common_stopword_overlap_alone_still_fires(self):
        """Both sentences share ONLY stopwords ('this', 'that', 'from',
        'should', 'have', 'value', 'these'); their real content words
        ('regarding'/'tempfile' vs 'concerning'/'bufferpool') are disjoint.
        Stopword-only overlap must not count as real grounding."""
        content = _content(
            "This should have that value from these regarding tempfile handling.",
            edits=["sources.py"],
        )
        snippet_cache = {
            "sources.py": (
                "This should have that value from these concerning bufferpool sizing."
            )
        }
        findings = _evaluate(content, snippet_cache)
        assert len(findings) == 1  # stopword-only overlap does not count


class TestNeverReadFileIsSilent:
    def test_edit_to_unread_file_produces_no_finding(self):
        """Delegated to grounding_validator.py's own 'unread edit target' check."""
        content = _content("I fixed the bug in a new helper file.", edits=["new_helper.py"])
        findings = _evaluate(content, snippet_cache={})
        assert findings == []


class TestToolOnlyResponse:
    def test_no_text_alongside_edit_produces_no_finding(self):
        content = [{"type": "tool_use", "name": "Edit", "input": {"file_path": "sources.py"}}]
        findings = _evaluate(content, snippet_cache={"sources.py": "class OrmResolver: pass"})
        assert findings == []


class TestNoEditsInResponse:
    def test_text_only_response_produces_no_finding(self):
        content = _content("Here is my analysis of the codebase.")
        findings = _evaluate(content, snippet_cache={})
        assert findings == []

    def test_empty_content_returns_empty(self):
        rule = ExplorationGroundingRule()
        assert rule.evaluate(
            content=None, messages=[], ctx=_Ctx(), tools=None,
            model="kimi-k2", session_snapshot={},
        ) == []


class TestMultipleEditsCheckedIndependently:
    def test_one_grounded_one_not(self):
        content = _content(
            "I updated OrmResolver.resolve_precedence in sources.py, and also "
            "tweaked the changelog with a small note about versioning.",
            edits=["sources.py", "CHANGELOG.md"],
        )
        snippet_cache = {
            "sources.py": "class OrmResolver:\n    def resolve_precedence(self, endpoint, pydantic_model):",
            "CHANGELOG.md": "## v1.2.0\n- Added payment_plan_id field to enrollment schema",
        }
        findings = _evaluate(content, snippet_cache)
        assert len(findings) == 1
        assert findings[0].subject == "CHANGELOG.md"


class TestRuleMetadata:
    def test_rule_is_agnostic_response_phase(self):
        rule = ExplorationGroundingRule()
        assert rule.agnostic is True
        assert rule.phase == "response"
        assert rule.id == "exploration_grounding"
