# ADR-0040: Exploration-Grounding Rule (and Why No New Orientation-Checkpoint Rule Ships)

**Status:** Accepted
**Date:** 2026-08-03
**Supersedes:** —
**Superseded by:** —

---

## Context

F-001 (`docs/findings/FINDINGS.md`) bundles two related directions for this
ADR:

- **Direction 3 — exploration-before-action grounding**: verify the model's
  immediate reasoning actually cites/uses prior exploration before acting on
  it, not just that exploration happened at some point in the conversation.
  ADR-0033 explicitly admits this gap in its own "Consequences" section:
  "the evidence check is conversation-wide and binary… this can under-flag (a
  search for something unrelated still counts) but never over-flags."
- **Direction 4 — forced orientation checkpoint**: every N tool calls in a
  broad-exploration phase, require explicit synthesis of what was learned
  before the next action.

Following the same "check before building" discipline ADR-0039 applied
(reuse existing detection rather than duplicate it), both directions were
checked against the current codebase before writing new rules.

**Direction 4 turned out to already be substantially shipped**, in two
places:
- `intent_classifier.py`'s **Override D** (`ctx.analysis_phase == "READ" and
  consecutive_reads >= self._synth_fallback` → forces `analysis_phase =
  "SYNTHESIZING"`) is exactly a general orientation checkpoint: after N
  consecutive reads in ANY analysis session (not just plan mode), the phase
  is forced to SYNTHESIZING, which `guardrail.py`'s
  `GuardrailTransformer.transform()` turns into a mandatory
  `_SYNTHESIS_PROMPT_WITH_TOOLS` system note requiring the model to
  synthesize before continuing.
- `intent_enforcement.py`'s **PLAN MODE NUDGE** (`ctx.analysis_read_count >=
  self._PLAN_NUDGE_THRESHOLD` while `plan_mode_active`) is the same
  mechanism specialized for plan-mode sessions specifically, firing earlier
  than Override D's generic threshold.

Building a third, parallel "orientation checkpoint" rule on top of these
would repeat the exact duplication risk ADR-0039 avoided. **This ADR does not
add a checkpoint rule for direction 4** — it documents that the gap is
already closed, by two independent, already-tested mechanisms, and moves on.

Direction 3 has no equivalent existing mechanism — `grounding_validator.py`
(ADR-0031/0033) checks *whether evidence exists* (a Read happened, a Grep
happened), never *whether the current reasoning engages with it*. This ADR
implements that missing rule: `exploration_grounding`.

## Decision

### `exploration_grounding` rule

`llm/state_assertion_rules/exploration_grounding.py`, `phase="response"`,
`agnostic=True`. For every file the model edits (`Edit`/`Write`/`MultiEdit`)
in the **current** response, checks whether the response's own reasoning text
shares any distinctive token (identifier-shaped word, length ≥ 4, common
stopwords excluded) with the actual content read from that file — using
`ctx.code_snippet_cache`, already populated by `GroundingValidatorTransformer`
(which always runs immediately before this rule in `build_response_pipeline`,
so the ordering dependency is structural, not incidental).

- If the file was **never read**, this rule stays silent — that's already
  `grounding_validator.py`'s Step 2.5 ("unread edit target") territory, a
  strictly weaker and already-covered question.
- If the file **was** read but the reasoning shares **zero** vocabulary with
  what was actually in it, `verdict="unverifiable"` (not `"contradicted"` —
  this doesn't prove the edit is wrong, only that engagement with the
  evidence can't be confirmed from the text), `severity="nudge"`.
- Deliberately lax threshold (`MIN_SHARED_TOKENS = 1`): the goal is only to
  catch the **zero-overlap** case — the same "weak signal, hedge accordingly"
  framing ADR-0033 already established, not a quality/sufficiency grade.

This is the concrete, deterministic check the source incident's damage
pattern calls for: `contract_schema.py`/`sources.py` in the incident had
correct names and structure but the plan's actual central logic (source
precedence, nested schema fields) was absent — code that reads as plausible
while ignoring the evidence, exactly what zero token-overlap between
reasoning and read-content would have flagged.

## Explicitly Out of Scope

- A new orientation-checkpoint rule for direction 4 — closed by Override D +
  the PLAN MODE NUDGE, as established above. If either mechanism proves
  insufficient in practice, that's a defect report against
  `intent_classifier.py`/`intent_enforcement.py`, not a gap for the
  state-assertion framework to fill redundantly.
- True semantic sufficiency checking ("does the edit correctly implement what
  was read?") — token overlap is a coarse proxy, not a correctness check;
  same explicit limitation ADR-0033 already accepted for its own weaker
  signal.
- Extending this rule beyond Edit/Write/MultiEdit targets (e.g. checking
  Bash-script content, or claims not tied to a file edit) — F-001 direction 3
  was scoped around "acting on unread/unengaged content," and file edits are
  the highest-severity instance of that (real code drift, not just a
  discussion claim).

## Consequences

- Closes the specific, admitted ADR-0033 gap for the one case with the
  highest real-world cost (silent code drift on an edited file), without
  reopening or modifying ADR-0031/0033/grounding_validator.py itself.
- Zero new false-positive risk for tool-only responses (no reasoning text to
  check) or for files never read (delegated to the existing, separate check).
- Residual, accepted limitation: token overlap is coarse — a model could
  reference an unrelated identifier from the snippet and avoid the flag while
  still not truly engaging with the evidence (same false-negative-biased
  design as every other rule in this framework: under-flag, never over-flag).
- No behavioral change to `intent_classifier.py`/`intent_enforcement.py` —
  this ADR only documents their existing coverage of direction 4, doesn't
  touch their code.

## Files Changed

- `vendor/claude-code-proxy/llm/state_assertion_rules/exploration_grounding.py`
  — new, the rule.
- `vendor/claude-code-proxy/llm/state_assertion_rules/__init__.py` — registers
  `exploration_grounding`.
- `vendor/claude-code-proxy/tests/test_exploration_grounding_rule.py` — new,
  rule unit tests.

## Verification

- Full proxy suite: 1282 passed (up from 1272 after ADR-0039; +10 new tests,
  0 regressions). Cumulative across ADR-0036–0040: 1216 → 1282 (+66 tests
  total for the whole state-assertion framework + its 3 rules), 0 regressions
  at any step.
- Rule tests confirm: an edit to a file whose read snippet shares a
  distinctive token with the response's reasoning does NOT fire; an edit to a
  read file whose snippet shares zero tokens with the reasoning DOES fire,
  `verdict="unverifiable"`; an edit to a file never read produces no finding
  (delegated to `grounding_validator.py`); a tool-only response (no text)
  produces no finding; multiple edited files are each checked independently;
  common stopword overlap alone does not count as grounding.
