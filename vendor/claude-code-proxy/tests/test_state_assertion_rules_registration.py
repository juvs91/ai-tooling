# tests/test_state_assertion_rules_registration.py
"""Tests for the explicit, declarative rule construction
(llm/state_assertion_rules/__init__.py, ADR-0038/0039/0040 amendment).

`build_active_rules()` is a pure factory: it returns a fresh
list[StateAssertionRule] built from ACTIVE_RULE_CLASSES and mutates no shared
state — no module-level registry exists to snapshot/restore, so these tests
need no cleanup fixture at all.
"""
import logging

from llm.state_assertion_rules import ACTIVE_RULE_CLASSES, build_active_rules
from llm.state_assertion_rules.deferred_denial import DeferredDenialRule
from llm.state_assertion_rules.no_progress import NoProgressRule
from llm.state_assertion_rules.exploration_grounding import ExplorationGroundingRule


class TestActiveRuleClassesIsTheSingleSourceOfTruth:
    def test_contains_exactly_the_three_shipped_rules(self):
        assert set(ACTIVE_RULE_CLASSES) == {
            DeferredDenialRule, NoProgressRule, ExplorationGroundingRule,
        }


class TestBuildActiveRules:
    def test_returns_expected_ids(self):
        rules = build_active_rules()
        ids = {r.id for r in rules}
        assert ids == {"deferred_denial", "no_progress", "exploration_grounding"}

    def test_returns_exactly_one_instance_per_active_class(self):
        rules = build_active_rules()
        assert len(rules) == len(ACTIVE_RULE_CLASSES)

    def test_calling_twice_mutates_nothing_shared(self):
        """Pure factory — two calls return independent lists with equivalent
        (but not identical) rule instances; neither call affects the other."""
        first = build_active_rules()
        second = build_active_rules()
        assert first is not second
        assert {r.id for r in first} == {r.id for r in second}
        assert all(a is not b for a, b in zip(first, second))


class TestBuildActiveRulesFaultTolerance:
    """A rule that fails to construct must be logged loudly (ERROR) and
    skipped — it must NOT crash build_active_rules() (which would crash
    whichever pipeline build called it, a worse outcome than building with
    one rule missing)."""

    def test_one_broken_class_does_not_prevent_others_from_building(self, monkeypatch, caplog):
        class _BrokenRule:
            def __init__(self):
                raise RuntimeError("construction always fails")

        monkeypatch.setattr(
            "llm.state_assertion_rules.ACTIVE_RULE_CLASSES",
            (DeferredDenialRule, _BrokenRule, NoProgressRule),
        )

        with caplog.at_level(logging.ERROR, logger="llm.state_assertion_rules"):
            rules = build_active_rules()  # must not raise

        ids = {r.id for r in rules}
        assert ids == {"deferred_denial", "no_progress"}
        assert any("_BrokenRule" in rec.message for rec in caplog.records)

    def test_count_mismatch_logs_warning(self, monkeypatch, caplog):
        class _BrokenRule:
            def __init__(self):
                raise RuntimeError("nope")

        monkeypatch.setattr(
            "llm.state_assertion_rules.ACTIVE_RULE_CLASSES",
            (DeferredDenialRule, _BrokenRule),
        )

        with caplog.at_level(logging.WARNING, logger="llm.state_assertion_rules"):
            build_active_rules()

        assert any("expected 2 rule(s), only 1 built" in rec.message for rec in caplog.records)

    def test_all_rules_healthy_logs_info_not_warning(self, caplog):
        with caplog.at_level(logging.INFO, logger="llm.state_assertion_rules"):
            build_active_rules()

        messages = [rec.message for rec in caplog.records]
        assert any("built 3 rule(s)" in m for m in messages)
        assert not any("expected" in m and "only" in m for m in messages)
