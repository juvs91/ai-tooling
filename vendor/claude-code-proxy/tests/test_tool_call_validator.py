# tests/test_tool_call_validator.py
"""Tests for ToolCallValidatorTransformer and AskUserQuestion auto-correction.

Covers all failure modes identified in the root-cause analysis:
  RC-1: model used 'question' (singular) as top-level key
  RC-2: model used 'questions' as a flat string instead of an array
  RC-3: missing required fields (header, options, multiSelect) per item
  RC-4: options with fewer than 2 items (minItems violation)
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.transformers.tool_call_validator import (
    _correct_ask_user_question,
    _ensure_question_item,
    _ensure_option,
    ToolCallValidatorTransformer,
)


# ── _ensure_option ────────────────────────────────────────────────────────────

class TestEnsureOption:
    def test_valid_option_unchanged(self):
        opt = {"label": "Yes", "description": "Proceed"}
        result = _ensure_option(opt)
        assert result["label"] == "Yes"
        assert result["description"] == "Proceed"

    def test_missing_description_uses_label(self):
        result = _ensure_option({"label": "Fast"})
        assert result["label"] == "Fast"
        assert result["description"] == "Fast"

    def test_missing_label_uses_default(self):
        result = _ensure_option({"description": "Some desc"})
        assert result["label"] == "Option"
        assert result["description"] == "Some desc"

    def test_non_dict_string_becomes_option(self):
        result = _ensure_option("Quick answer")
        assert result["label"] == "Quick answer"
        assert result["description"] == "Quick answer"

    def test_non_dict_none_becomes_default(self):
        result = _ensure_option(None)
        assert result["label"] == "Option"

    def test_label_truncated_to_30_chars(self):
        long = "A" * 50
        result = _ensure_option({"label": long, "description": "d"})
        assert len(result["label"]) == 30


# ── _ensure_question_item ────────────────────────────────────────────────────

class TestEnsureQuestionItem:
    def _valid_item(self):
        return {
            "question": "Which approach?",
            "header": "Approach",
            "options": [
                {"label": "A", "description": "Fast"},
                {"label": "B", "description": "Safe"},
            ],
            "multiSelect": False,
        }

    def test_valid_item_passes_through(self):
        item = self._valid_item()
        result = _ensure_question_item(item)
        assert result["question"] == "Which approach?"
        assert result["header"] == "Approach"
        assert len(result["options"]) == 2
        assert result["multiSelect"] is False

    def test_missing_header_gets_default(self):
        item = self._valid_item()
        del item["header"]
        result = _ensure_question_item(item)
        assert result["header"] == "Question"

    def test_missing_options_gets_two_defaults(self):
        item = self._valid_item()
        del item["options"]
        result = _ensure_question_item(item)
        assert len(result["options"]) >= 2

    def test_empty_options_gets_two_defaults(self):
        item = self._valid_item()
        item["options"] = []
        result = _ensure_question_item(item)
        assert len(result["options"]) >= 2

    def test_single_option_padded_to_two(self):
        """minItems: 2 — single option must be padded."""
        item = self._valid_item()
        item["options"] = [{"label": "Only", "description": "desc"}]
        result = _ensure_question_item(item)
        assert len(result["options"]) == 2

    def test_options_truncated_to_four(self):
        """maxItems: 4 — extra options are dropped."""
        item = self._valid_item()
        item["options"] = [{"label": str(i), "description": "d"} for i in range(6)]
        result = _ensure_question_item(item)
        assert len(result["options"]) == 4

    def test_missing_multiselect_defaults_false(self):
        item = self._valid_item()
        del item["multiSelect"]
        result = _ensure_question_item(item)
        assert result["multiSelect"] is False

    def test_string_multiselect_truthy_coerced(self):
        item = self._valid_item()
        item["multiSelect"] = "true"
        result = _ensure_question_item(item)
        assert result["multiSelect"] is True

    def test_string_multiselect_falsy_coerced(self):
        item = self._valid_item()
        item["multiSelect"] = "false"
        result = _ensure_question_item(item)
        assert result["multiSelect"] is False

    def test_header_over_12_chars_truncated(self):
        """header is truncated to 12 chars to match the real CC schema constraint."""
        item = self._valid_item()
        item["header"] = "A" * 20
        result = _ensure_question_item(item)
        assert result["header"] == "A" * 12

    def test_non_dict_item_uses_fallback_text(self):
        result = _ensure_question_item("Is this correct?")
        assert result["question"] == "Is this correct?"
        assert len(result["options"]) >= 2


# ── _correct_ask_user_question ────────────────────────────────────────────────

class TestCorrectAskUserQuestion:

    def _minimal_valid(self):
        return {
            "questions": [{
                "question": "Which approach?",
                "header": "Approach",
                "options": [
                    {"label": "A", "description": "Fast"},
                    {"label": "B", "description": "Safe"},
                ],
                "multiSelect": False,
            }]
        }

    # ── RC-1: 'question' used as top-level key ─────────────────────────────

    def test_rc1_singular_question_key_is_wrapped(self):
        """RC-1: {"question": "…"} → {"questions": [{…}]}"""
        bad = {"question": "Which style?"}
        corrected, corrections = _correct_ask_user_question(bad)
        assert "questions" in corrected
        assert isinstance(corrected["questions"], list)
        assert len(corrected["questions"]) == 1
        assert corrected["questions"][0]["question"] == "Which style?"
        assert any("singular" in c or "question" in c for c in corrections)

    def test_rc1_singular_carries_over_other_keys(self):
        """Item-level keys alongside 'question' should be preserved in the corrected item."""
        bad = {
            "question": "Which style?",
            "header": "Style",
            "options": [{"label": "A", "description": "d1"}, {"label": "B", "description": "d2"}],
            "multiSelect": True,
        }
        corrected, _ = _correct_ask_user_question(bad)
        item = corrected["questions"][0]
        assert item["header"] == "Style"
        assert item["multiSelect"] is True
        assert len(item["options"]) == 2

    # ── RC-2: 'questions' used as a flat string ─────────────────────────────

    def test_rc2_questions_as_string_converted(self):
        """RC-2: {"questions": "Which approach?"} → {"questions": [{…}]}"""
        bad = {"questions": "Which approach should we use?"}
        corrected, corrections = _correct_ask_user_question(bad)
        assert isinstance(corrected["questions"], list)
        assert corrected["questions"][0]["question"] == "Which approach should we use?"
        assert any("string" in c for c in corrections)

    def test_rc2_questions_as_string_gets_default_options(self):
        bad = {"questions": "Should we proceed?"}
        corrected, _ = _correct_ask_user_question(bad)
        item = corrected["questions"][0]
        assert len(item["options"]) >= 2

    # ── Valid input — no corrections ────────────────────────────────────────

    def test_valid_input_produces_no_corrections(self):
        good = self._minimal_valid()
        corrected, corrections = _correct_ask_user_question(good)
        assert corrections == []
        assert corrected["questions"][0]["question"] == "Which approach?"

    def test_valid_multi_question_input_untouched(self):
        good = {
            "questions": [
                {"question": "Q1?", "header": "Q1", "multiSelect": False,
                 "options": [{"label": "A", "description": "d"}, {"label": "B", "description": "d"}]},
                {"question": "Q2?", "header": "Q2", "multiSelect": True,
                 "options": [{"label": "X", "description": "d"}, {"label": "Y", "description": "d"}]},
            ]
        }
        corrected, corrections = _correct_ask_user_question(good)
        assert corrections == []
        assert len(corrected["questions"]) == 2

    # ── RC-3: missing required fields per item ──────────────────────────────

    def test_rc3_missing_header_patched(self):
        bad = {"questions": [{"question": "Q?",
                              "options": [{"label": "A", "description": "d"},
                                          {"label": "B", "description": "d"}],
                              "multiSelect": False}]}
        corrected, corrections = _correct_ask_user_question(bad)
        assert corrected["questions"][0]["header"] == "Question"
        assert any("item[0]" in c for c in corrections)

    def test_rc3_missing_options_gets_defaults(self):
        bad = {"questions": [{"question": "Q?", "header": "H", "multiSelect": False}]}
        corrected, corrections = _correct_ask_user_question(bad)
        opts = corrected["questions"][0]["options"]
        assert len(opts) >= 2

    def test_rc3_missing_multiselect_defaults_false(self):
        bad = {"questions": [{"question": "Q?", "header": "H",
                              "options": [{"label": "A", "description": "d"},
                                          {"label": "B", "description": "d"}]}]}
        corrected, corrections = _correct_ask_user_question(bad)
        assert corrected["questions"][0]["multiSelect"] is False

    # ── RC-4: minItems violation ────────────────────────────────────────────

    def test_rc4_single_option_padded(self):
        bad = {"questions": [{"question": "Q?", "header": "H", "multiSelect": False,
                              "options": [{"label": "Only", "description": "d"}]}]}
        corrected, _ = _correct_ask_user_question(bad)
        assert len(corrected["questions"][0]["options"]) == 2

    # ── Edge cases ──────────────────────────────────────────────────────────

    def test_empty_dict_produces_placeholder(self):
        corrected, corrections = _correct_ask_user_question({})
        assert isinstance(corrected["questions"], list)
        assert len(corrected["questions"]) >= 1
        assert corrections  # at least one correction applied

    def test_none_input_produces_placeholder(self):
        corrected, corrections = _correct_ask_user_question(None)
        assert isinstance(corrected["questions"], list)

    def test_questions_non_list_non_string_wrapped(self):
        bad = {"questions": 42}
        corrected, corrections = _correct_ask_user_question(bad)
        assert isinstance(corrected["questions"], list)

    def test_questions_truncated_to_four(self):
        bad = {
            "questions": [
                {"question": f"Q{i}?", "header": "H", "multiSelect": False,
                 "options": [{"label": "A", "description": "d"},
                             {"label": "B", "description": "d"}]}
                for i in range(6)
            ]
        }
        corrected, _ = _correct_ask_user_question(bad)
        assert len(corrected["questions"]) == 4


# ── ToolCallValidatorTransformer ─────────────────────────────────────────────

class TestToolCallValidatorTransformer:

    def _make_ctx(self):
        ctx = MagicMock()
        ctx.tools = [{"name": "AskUserQuestion"}]
        return ctx

    def _make_request(self, content):
        req = SimpleNamespace(content=content)
        return req

    @pytest.mark.asyncio
    async def test_rc1_corrects_singular_question_key(self):
        """RC-1: transformer corrects {"question":"…"} in-place."""
        block = {"type": "tool_use", "name": "AskUserQuestion", "id": "tu-1",
                 "input": {"question": "Which approach?"}}
        req = self._make_request([block])
        transformer = ToolCallValidatorTransformer()
        await transformer.transform(req, self._make_ctx())

        assert "questions" in block["input"]
        assert isinstance(block["input"]["questions"], list)
        assert block["input"]["questions"][0]["question"] == "Which approach?"

    @pytest.mark.asyncio
    async def test_rc2_corrects_questions_as_string(self):
        """RC-2: transformer corrects {"questions":"…"} in-place."""
        block = {"type": "tool_use", "name": "AskUserQuestion", "id": "tu-2",
                 "input": {"questions": "Which approach should we use?"}}
        req = self._make_request([block])
        transformer = ToolCallValidatorTransformer()
        await transformer.transform(req, self._make_ctx())

        assert isinstance(block["input"]["questions"], list)
        assert block["input"]["questions"][0]["question"] == "Which approach should we use?"

    @pytest.mark.asyncio
    async def test_valid_input_not_modified(self):
        """Valid input must not be mutated."""
        original = {
            "questions": [{
                "question": "Q?", "header": "H", "multiSelect": False,
                "options": [{"label": "A", "description": "d"},
                            {"label": "B", "description": "d"}],
            }]
        }
        import copy
        block = {"type": "tool_use", "name": "AskUserQuestion", "id": "tu-3",
                 "input": copy.deepcopy(original)}
        req = self._make_request([block])
        transformer = ToolCallValidatorTransformer()
        await transformer.transform(req, self._make_ctx())

        assert block["input"] == original

    @pytest.mark.asyncio
    async def test_non_ask_user_question_not_touched(self):
        """Other tool_use blocks must not be modified."""
        block = {"type": "tool_use", "name": "Bash", "id": "tu-4",
                 "input": {"command": "ls -la"}}
        req = self._make_request([block])
        transformer = ToolCallValidatorTransformer()
        await transformer.transform(req, self._make_ctx())

        assert block["input"] == {"command": "ls -la"}

    @pytest.mark.asyncio
    async def test_no_content_is_noop(self):
        """Request with no content attribute must not raise."""
        req = SimpleNamespace()
        transformer = ToolCallValidatorTransformer()
        await transformer.transform(req, self._make_ctx())  # must not raise

    @pytest.mark.asyncio
    async def test_non_list_content_is_noop(self):
        req = self._make_request("plain text response")
        transformer = ToolCallValidatorTransformer()
        await transformer.transform(req, self._make_ctx())  # must not raise

    @pytest.mark.asyncio
    async def test_multiple_blocks_only_ask_user_corrected(self):
        """In a mixed-content response, only AskUserQuestion blocks are corrected."""
        bash_block = {"type": "tool_use", "name": "Bash", "id": "b1",
                      "input": {"command": "ls"}}
        ask_block  = {"type": "tool_use", "name": "AskUserQuestion", "id": "a1",
                      "input": {"question": "Which approach?"}}
        text_block = {"type": "text", "text": "Thinking…"}

        req = self._make_request([bash_block, text_block, ask_block])
        transformer = ToolCallValidatorTransformer()
        await transformer.transform(req, self._make_ctx())

        assert bash_block["input"] == {"command": "ls"}
        assert "questions" in ask_block["input"]
        assert isinstance(ask_block["input"]["questions"], list)


# ── Deferred tools schema regression (RC-3 / RC-4) ───────────────────────────

class TestDeferredToolsSchemaCompleteness:
    """Verify the proxy-injected AskUserQuestion schema matches the real CC schema."""

    def _get_injected_schema(self):
        from llm.transformers.deferred_tools import _CC_TOOL_SCHEMAS
        return _CC_TOOL_SCHEMAS["AskUserQuestion"]

    def test_questions_is_required_at_top_level(self):
        schema = self._get_injected_schema()
        assert "questions" in schema.get("required", [])

    def test_questions_is_array_type(self):
        schema = self._get_injected_schema()
        assert schema["properties"]["questions"]["type"] == "array"

    def test_questions_has_max_items_4(self):
        schema = self._get_injected_schema()
        assert schema["properties"]["questions"].get("maxItems") == 4

    def test_item_required_fields_match_real_schema(self):
        """All four fields required by Claude Code's real validator must be marked required."""
        schema = self._get_injected_schema()
        item_required = set(schema["properties"]["questions"]["items"].get("required", []))
        assert item_required == {"question", "header", "options", "multiSelect"}, (
            f"Expected required=[question, header, options, multiSelect], got {item_required}"
        )

    def test_options_has_min_items_2(self):
        schema = self._get_injected_schema()
        opts = schema["properties"]["questions"]["items"]["properties"]["options"]
        assert opts.get("minItems") == 2

    def test_options_has_max_items_4(self):
        schema = self._get_injected_schema()
        opts = schema["properties"]["questions"]["items"]["properties"]["options"]
        assert opts.get("maxItems") == 4

    def test_option_items_require_label_and_description(self):
        schema = self._get_injected_schema()
        opts_items = (
            schema["properties"]["questions"]["items"]["properties"]["options"]["items"]
        )
        item_required = set(opts_items.get("required", []))
        assert {"label", "description"}.issubset(item_required)


# ── Description quality checks (RC-1 / RC-2) ─────────────────────────────────

class TestDeferredToolsDescriptionQuality:
    """Verify the AskUserQuestion description communicates the structure clearly."""

    def _get_description(self):
        from llm.transformers.deferred_tools import _CC_TOOL_DESCRIPTIONS
        return _CC_TOOL_DESCRIPTIONS["AskUserQuestion"]

    def test_description_mentions_questions_plural_key(self):
        desc = self._get_description()
        assert "questions" in desc

    def test_description_says_array(self):
        desc = self._get_description()
        assert "array" in desc.lower() or "ARRAY" in desc

    def test_description_warns_not_question_singular(self):
        """Description must steer models away from the 'question' (singular) trap."""
        desc = self._get_description()
        # Should mention the singular form to warn against it
        assert "question" in desc.lower()

    def test_description_includes_json_example(self):
        """A concrete JSON example must be embedded in the description."""
        desc = self._get_description()
        assert '{"questions"' in desc or '"questions":' in desc

    def test_description_mentions_all_required_fields(self):
        desc = self._get_description()
        for field in ("header", "options", "multiSelect"):
            assert field in desc, f"description missing mention of required field '{field}'"

    def test_description_used_in_new_defs(self):
        """new_defs must use _CC_TOOL_DESCRIPTIONS, not a hardcoded generic string."""
        import inspect
        from llm.transformers.deferred_tools import DeferredToolsTransformer
        src = inspect.getsource(DeferredToolsTransformer.transform)
        assert "_CC_TOOL_DESCRIPTIONS" in src, (
            "DeferredToolsTransformer.transform must use _CC_TOOL_DESCRIPTIONS for descriptions"
        )
