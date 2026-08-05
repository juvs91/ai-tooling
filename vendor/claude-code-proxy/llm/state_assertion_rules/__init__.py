# llm/state_assertion_rules/__init__.py
"""Explicit, declarative construction of state-assertion rules (ADR-0038/0039/0040).

Two revisions before this one:
1. Each rule module appended itself to a module-level `RULES_REGISTRY` in
   llm/state_assertion.py as an import-time side effect — "which rules are
   active" wasn't grep-able from one place, and a broken import chain would
   leave the registry silently empty.
2. `register_rules()` made activation explicit (this file's
   ACTIVE_RULE_CLASSES + a single call site) but still mutated a shared
   module-level global that `evaluate_rules()` read implicitly — any test or
   future caller could accidentally read/mutate it, and every test that
   touched it needed its own snapshot/clear/restore fixture.

This revision: no global at all. `build_active_rules()` is a pure factory —
it returns a fresh `list[StateAssertionRule]`, built from ACTIVE_RULE_CLASSES,
and does not touch any shared state anywhere. The caller (proxy/proxy.py)
owns the returned list and passes it explicitly into each transformer's
constructor. `llm.state_assertion.evaluate_rules()` takes `rules` as a
parameter — it has no global of its own to read.
"""
from __future__ import annotations

import logging

from llm.state_assertion import StateAssertionRule
from llm.state_assertion_rules.deferred_denial import DeferredDenialRule
from llm.state_assertion_rules.no_progress import NoProgressRule
from llm.state_assertion_rules.exploration_grounding import ExplorationGroundingRule

logger = logging.getLogger(__name__)

# The single declarative list of active rules. This is the only place that
# needs to change to add, remove, or reorder a rule.
ACTIVE_RULE_CLASSES: tuple[type, ...] = (
    DeferredDenialRule,
    NoProgressRule,
    ExplorationGroundingRule,
)


def build_active_rules() -> list[StateAssertionRule]:
    """Construct one instance per class in ACTIVE_RULE_CLASSES and return them.

    Pure factory — returns a fresh list, mutates nothing shared. Safe to call
    more than once (e.g. once per pipeline build); rules are stateless so
    re-constructing them is cheap and harmless.

    Deliberately does NOT let one rule's construction failure crash the
    caller — that would turn "one broken rule" into "the entire proxy won't
    build a pipeline," a worse outcome than degrading gracefully. Instead:
    each failure is logged at ERROR (loud, actionable, visible in logs), the
    remaining rules still get built, and a final count check logs a WARNING
    if fewer rules were built than declared — so a broken rule is always
    visible in logs, never silent, without being a single point of total
    failure the way a hard `raise` here would be.
    """
    rules: list[StateAssertionRule] = []
    for cls in ACTIVE_RULE_CLASSES:
        try:
            rules.append(cls())
        except Exception as exc:
            logger.error(
                "[state-assertion] failed to build rule %s: %s: %s — "
                "proxy will run without it",
                cls.__name__, type(exc).__name__, exc,
            )

    built_ids = [r.id for r in rules]
    if len(rules) != len(ACTIVE_RULE_CLASSES):
        logger.warning(
            "[state-assertion] expected %d rule(s), only %d built: %s",
            len(ACTIVE_RULE_CLASSES), len(rules), built_ids,
        )
    else:
        logger.info(
            "[state-assertion] built %d rule(s): %s",
            len(rules), built_ids,
        )
    return rules
