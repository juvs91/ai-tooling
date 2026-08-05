# ADR-0036: State-Assertion Verification Framework

**Status:** Accepted
**Date:** 2026-08-03
**Supersedes:** —
**Superseded by:** —

---

## Context

`docs/findings/FINDINGS.md` F-001 documents Kimi K2 (and non-Claude models in
general) failing at a specific reasoning task: orchestrating a broad search
space (reading a repo, fetching the web, deciding the next tool, acting
congruently with what was learned). The most severe symptom: the model does
not connect context it itself generated/read — present in the same
conversation with no compression in between — with the immediate decision in
front of it. F-001 boceta 6 direcciones de solución, not yet decided:

1. Deterministic nudge when the model denies having a tool that actually
   exists (deferred).
2. Generalize the "no-progress loop" heuristic (ADR-0009, today limited to
   `Read`) to any tool family.
3. "Exploration-before-action" grounding: verify the model's immediate
   reasoning actually cites/uses prior exploration, not just that exploration
   happened at some point (closes the gap ADR-0033 admits: "evidence check is
   binary and topic-blind").
4. Forced "orientation checkpoint" every N tool calls in broad-exploration
   phases.
5. Never accept a textual state assertion without technical verification
   (general principle).
6. Cheap, already-evidenced mitigation for the plan-mode-specific case (see
   ADR-0037).

This ADR examines these 6 directions together, before designing any of them
individually, and confirms they share one structural signature — already
implemented three times independently in this proxy:

| Instance | Model's assertion (A) | Real, computable signal (S) | Where it lives today |
|---|---|---|---|
| ADR-0031 | "ya arreglé X" | An Edit/Write/MultiEdit targeted X somewhere in the conversation | `grounding_validator.py`, completion-claim block |
| ADR-0033 | "todos los callers respetan X" | A Grep/Glob/Bash-grep ran somewhere in the conversation | `grounding_validator.py`, generality-claim block |
| This incident | "I don't have that tool" / "user says I'm in plan mode" | The tool is in the deferred catalog / `ctx.plan_mode_active` | Nowhere — this is the gap |

`GroundingValidatorTransformer` already implements the pattern **claim
detection (regex) → evidence cross-check → session-cache persistence →
threshold-based escalation** twice, by hand, for two different claim shapes.
A third hand-copy for direction 1 (and a fourth, fifth for directions 2-4)
would be the same template implemented five times independently — exactly the
kind of duplication `AGENTS.md`'s deduplication mandate exists to prevent, and
the kind of drift ADR-0032 already fixed once for session-state modules
(`compressor.py` → `llm/session/*`).

This ADR designs the shared core once: **a state-assertion verification
framework** that directions 1-4 (and, implicitly, 5) register into as rules,
rather than five independent transformers. Concrete rules (ADR-0038
`deferred_denial`, ADR-0039 `no_progress`, ADR-0040
`exploration`/`orientation`) ship in follow-up ADRs against this framework;
this ADR ships the framework itself with an **empty rule registry** — pure
plumbing, verified end-to-end with synthetic test rules, with zero behavior
change to any existing request/response until a rule is registered.

## Decision

### 1. Core module — `llm/state_assertion.py` (pure, no I/O)

```python
@dataclass(frozen=True)
class AssertionFinding:
    rule_id: str
    verdict: str            # "contradicted" | "unverifiable"
    evidence_snippet: str
    subject: str
    correction_note: str
    severity: str            # "block" | "refine" | "nudge" | "log"

class StateAssertionRule(Protocol):
    id: str
    phase: Literal["request", "response"]
    agnostic: bool            # True = runs for every model; False = fragile-only
    def evaluate(self, *, content, messages, ctx, tools, model, session_snapshot) -> list[AssertionFinding]: ...

RULES_REGISTRY: list[StateAssertionRule] = []   # empty in this ADR

def evaluate_rules(phase, *, content, messages, ctx, tools, model, session_snapshot) -> list[AssertionFinding]:
    ...  # filters by phase, gates non-agnostic rules by is_fragile_orchestration_model(model)
```

`agnostic` reuses `is_fragile_orchestration_model` (ADR-0037) as the gate for
non-agnostic rules — the same split the plan design settled on: cheap
detection+nudge rules run for every model (matching
`quality_refinement.py`'s deliberately agnostic design), while
expensive/perturbing interventions stay opt-in per model.

`evaluate()` is synchronous by contract — rules cannot `await`. Anything a
rule needs from session cache for escalation is fetched by the calling shell
(below) **before** invoking `evaluate_rules()` and handed in via
`session_snapshot={"assertion_events": [...]}` (raw entries from
`get_session_assertion_events`).

**Fault isolation (added after review, before this ADR's code was
considered done):** `evaluate_rules()` wraps each rule's `evaluate()` call in
its own `try/except`, logs a warning, and continues to the next rule on
failure. This is not optional hardening — `Pipeline.process()`
(`llm/pipeline.py`) logs and **re-raises** any exception a transformer lets
escape, and on the non-streaming response path (where
`StateAssertionResponseTransformer` runs) there is no fallback above it: the
exception propagates through `_run_response_pipeline` up to `server.py`'s
generic error handler, which converts it into an HTTP error response —
**discarding the real, already-generated model response** for that turn, not
just skipping a nudge. A single bad rule (a regex hitting an unexpected input
shape, a missing dict key, etc.) must never be able to do that, nor prevent
every other registered rule from running. Verified with dedicated tests
(`TestEvaluateRulesFaultIsolation` in `tests/test_state_assertion.py`): a
raising rule contributes no findings and does not propagate, regardless of
its position in the registry or the exception type, and every other rule
still runs.

### 2. Two thin pipeline adapters, not a monolith and not five transformers

- `StateAssertionRequestTransformer` (`llm/transformers/state_assertion_request.py`)
  — registered in `build_request_pipeline` (`proxy/proxy.py`) right after
  `IntentClassifierTransformer` (to read `ctx.plan_mode_active`/`ctx.intent`,
  already computed) and before `PlanModeEnforcementTransformer`. Runs
  `phase="request"` rules and injects findings via `ensure_system_note(request, ...)`
  — the **request** object, which has a real `.system` field the model sees.
- `StateAssertionResponseTransformer` (`llm/transformers/state_assertion_response.py`)
  — registered in `build_response_pipeline` right after
  `GroundingValidatorTransformer` and before `ModelFeedbackTransformer`. Runs
  `phase="response"` rules against the model's own output.

Both populate `ctx.state_assertion_findings` (new `TransformContext` field,
`llm/pipeline.py`) for observability, and fire-and-forget persist each finding
to session cache via `append_session_assertion_event`.

**Second correction, made after review of the shipped code (rule activation
mechanism):** the first version of ADR-0038/0039/0040 had each rule module
append itself to `RULES_REGISTRY` as an import-time side effect (the bottom
of `deferred_denial.py` etc. did `RULES_REGISTRY.append(DeferredDenialRule())`
directly, and `llm/state_assertion_rules/__init__.py` just imported the three
modules to trigger it). Reworked to be explicit and declarative instead:
`llm/state_assertion_rules/__init__.py` now defines `ACTIVE_RULE_CLASSES` (a
plain tuple — the single, grep-able source of truth for what's active) and
`register_rules()`, the single explicit call site (`proxy/proxy.py` calls it
once at process boot). Rule modules no longer touch `RULES_REGISTRY` at all —
they only define their class. `register_rules()` is idempotent (clears before
rebuilding) and never lets one rule's construction failure crash the whole
proxy at boot: a per-class `try/except` logs at ERROR and skips just that
rule, then a final count check logs at WARNING if the registry ended up
smaller than expected, or at INFO with the exact registered IDs on success.
This closes two real gaps in the original design: (1) a broken import chain
(e.g. this package no longer imported from `proxy.py`) previously left
`RULES_REGISTRY` silently empty — no error, no log; now a working chain always
logs at boot, so its absence is conspicuous; (2) "which rules are active" was
previously only discoverable by reading three separate files' import side
effects — now it's one tuple in one file. Verified with
`tests/test_state_assertion_rules_registration.py`: expected IDs populate,
idempotency, a broken rule class is isolated and logged without crashing
registration, count mismatches log a warning.

**Third correction, made after further review of the same activation
mechanism:** even with explicit `register_rules()`, `RULES_REGISTRY` was
still a shared, importable, mutable module-level list in `llm/state_assertion.py`
that `evaluate_rules()` read implicitly — any test or future caller could
still read or mutate it directly, and every test file that touched it needed
its own snapshot/clear/restore fixture. Reworked to full dependency
injection: `RULES_REGISTRY` was removed entirely.
`llm/state_assertion_rules/build_active_rules()` (renamed from
`register_rules()`) is now a pure factory — it returns a fresh
`list[StateAssertionRule]`, mutating nothing shared. `evaluate_rules()` takes
`rules` as an explicit parameter instead of reading a global.
`StateAssertionRequestTransformer`/`StateAssertionResponseTransformer` gained
constructors (`__init__(self, rules)`) and store `self._rules`.
`proxy/proxy.py` builds the list once at module import
(`_STATE_ASSERTION_RULES = build_active_rules()`) and passes that same list
object into both transformers' constructors wherever
`build_request_pipeline`/`build_response_pipeline` instantiate them —
including on the response side, where `_run_response_pipeline` reconstructs
the whole `Pipeline` (and thus a new `StateAssertionResponseTransformer`
instance) **on every request** (verified by reading `_run_response_pipeline`'s
docstring and call sites — it is the "single canonical call site shared by
LiteLLM non-stream and passthrough non-stream," called per-request, not
cached). Passing the same rules list into each fresh transformer means rule
instances are still built exactly once per process, never per request. This
change eliminated the snapshot/clear/restore fixture from every test file
that touches rules — tests now build a local list and pass it directly,
with zero shared state to leak between tests. Verified with a real process
boot (`python -c "import proxy.proxy; ..."`): the request and response
pipeline builders' `StateAssertionRequestTransformer`/
`StateAssertionResponseTransformer` instances both hold `_rules is
_STATE_ASSERTION_RULES` (identity, not just equality) — the same list object,
confirmed via `tests/test_state_assertion_transformers.py::TestPipelineRegistration::test_real_pipeline_transformers_carry_the_active_rules`.

**Important correction made during implementation, not in the original
design sketch:** `grounding_validator.py` calls
`ensure_system_note(ctx, ...)` (passing the `TransformContext`, not the
request) in three places (ADR-0031/0033's completion/generality-claim nudges).
`TransformContext` has no `system` attribute, so `ensure_system_note` falls
into its "system is None → setattr" branch and writes an attribute nothing
ever reads — **this call is a documented no-op already present in production
code**, not something this ADR introduces. The real, working channel for
response-phase correction is `ctx.grounding_issues` (a `list[str]`), which
`quality_refinement.py`'s `_build_grounding_feedback()` already consumes to
build feedback text for a refinement re-request — this is the actual
mechanism ADR-0031/0033 rely on in practice. `StateAssertionResponseTransformer`
appends `f"[state-assertion:{rule_id}] {correction_note}"` to
`ctx.grounding_issues` directly, rather than repeating the no-op call. Fixing
the pre-existing no-op in `grounding_validator.py` itself is out of scope for
this ADR (changing ADR-0031/0033's shipped behavior needs its own decision).

### 3. Session persistence — mirrors `generality_claims.py` exactly

- `llm/session/state_assertion_cache.py` — `append_session_assertion_event`,
  `get_session_assertion_events`. (An earlier draft of this ADR also added a
  `count_recent_assertion_events` convenience helper for future escalation
  checks — removed before merge: no rule used it, and rules that need this
  can filter `session_snapshot["assertion_events"]` themselves, since the
  shells already fetch and hand them the raw list synchronously. Adding it
  back is cheap once a rule actually needs it — YAGNI, per `AGENTS.md`.)
- New `_CompressionCache.state_assertion_events: list[dict]` field
  (`llm/session/store.py`), capped at 50, included in disk save/load — same
  pattern as `completion_claims`/`generality_claims`.
- Re-exported from `llm/compressor.py`'s facade (ADR-0032's convention: only
  `compressor.py` imports directly from `llm/session/*`; every transformer
  imports from the facade).

### 4. Why this doesn't reopen ADR-0032

ADR-0032 decomposed `compressor.py`'s session state into `llm/session/*`
specifically to stop the "everything lives in one 1487-line file" pattern.
This ADR follows that decomposition's own convention exactly (new
`llm/session/state_assertion_cache.py` module, facade re-export) rather than
adding a seventh field-cluster inline anywhere else.

## Explicitly Out of Scope

- Any concrete rule (`deferred_denial`, `no_progress`, `exploration`,
  `orientation`) — `RULES_REGISTRY` ships empty. See ADR-0038/0039/0040.
- Fixing `ensure_system_note(ctx, ...)`'s no-op in `grounding_validator.py`
  for ADR-0031/0033's existing nudges — documented above as a pre-existing
  condition, not introduced or fixed here.
- Migrating ADR-0031/0033's completion/generality-claim logic to be
  registry-based rules on this same framework — a plausible future
  consolidation (mentioned as an option in the original design exploration),
  deliberately deferred: this ADR is purely additive, zero risk to grounding
  behavior already in production.
- Eager plan-mode tool loading for fragile models — that's ADR-0037, already
  shipped independently, no dependency in either direction.

## Consequences

- Zero behavior change to any existing request/response today —
  `RULES_REGISTRY` is empty, so both new transformers are no-ops in
  production until ADR-0038 registers the first rule. Verified: full proxy
  suite unchanged in count/outcome from the ADR-0037 baseline plus the new
  tests below, all passing.
- Every future direction from F-001 (1-4) becomes an additive registry entry
  — a new rule object plus its own tests — never a new transformer, a new
  pipeline registration, or a new session-cache module.
- Residual, accepted risk: the framework itself has no rules yet, so it
  provides no protection on its own. Its value is entirely in what ADR-0038/
  0039/0040 register into it — this ADR is deliberately "pay for the
  abstraction once, validate it with 1-2 real rules before adding the rest,"
  per the plan's original over-engineering guard.

## Files Changed

- `vendor/claude-code-proxy/llm/state_assertion.py` — new, core (rule
  protocol, empty registry, `evaluate_rules` engine).
- `vendor/claude-code-proxy/llm/session/state_assertion_cache.py` — new,
  session persistence.
- `vendor/claude-code-proxy/llm/session/store.py` — new `_CompressionCache`
  field `state_assertion_events` + disk save/load.
- `vendor/claude-code-proxy/llm/compressor.py` — re-export of the three new
  session functions.
- `vendor/claude-code-proxy/llm/pipeline.py` — new `TransformContext` field
  `state_assertion_findings`.
- `vendor/claude-code-proxy/llm/transformers/state_assertion_request.py` —
  new, request-phase shell.
- `vendor/claude-code-proxy/llm/transformers/state_assertion_response.py` —
  new, response-phase shell.
- `vendor/claude-code-proxy/llm/transformers/__init__.py` — export both new
  transformers.
- `vendor/claude-code-proxy/proxy/proxy.py` — register both transformers in
  `build_request_pipeline`/`build_response_pipeline`; calls
  `register_rules()` once at module load (explicit activation, see above).
- `vendor/claude-code-proxy/llm/state_assertion_rules/__init__.py` —
  `ACTIVE_RULE_CLASSES` (declarative list) + `register_rules()` (explicit,
  idempotent, fault-tolerant activation — see above).
- `vendor/claude-code-proxy/tests/test_state_assertion.py` — engine unit
  tests (phase filtering, agnostic/fragile gating, aggregation, per-rule
  fault isolation) via synthetic dummy rules.
- `vendor/claude-code-proxy/tests/test_state_assertion_cache.py` — session
  persistence unit tests.
- `vendor/claude-code-proxy/tests/test_state_assertion_transformers.py` —
  integration tests for both shells (system-note target correctness,
  `ctx.grounding_issues` channel, session-event scheduling, severity gating,
  real pipeline registration order via `build_request_pipeline`/
  `build_response_pipeline`).
- `vendor/claude-code-proxy/tests/test_state_assertion_rules_registration.py`
  — new, tests for `register_rules()`/`ACTIVE_RULE_CLASSES`.
- `vendor/claude-code-proxy/tests/test_pipeline.py` — updated expected-fields
  set (`state_assertion_findings`).

## Verification

- Full proxy suite: 1243 passed right after this ADR's initial merge (up
  from the 1216 baseline after ADR-0037; +27 new tests, 0 regressions); 1290
  passed after the two post-review corrections documented above (fault
  isolation in `evaluate_rules()`, explicit/declarative rule registration) —
  still 0 regressions.
- Dedicated engine tests confirm: empty registry is a true no-op; phase
  filtering is exact (a `phase="request"` rule never runs during response
  evaluation and vice versa); `agnostic=False` rules are skipped for
  non-fragile models and run for fragile ones; multiple matching rules all
  contribute findings; `AssertionFinding` is immutable; a rule that raises is
  isolated (doesn't propagate, doesn't block other rules, order-independent,
  across multiple exception types).
- Sanity-checked against a real process boot (`python -c "import proxy.proxy"`
  with logging enabled): logs
  `[state-assertion] registered 3 rule(s): ['deferred_denial', 'no_progress',
  'exploration_grounding']` at INFO — confirms the explicit activation path
  works end-to-end, not just in unit tests.
- Dedicated transformer tests confirm: request-phase findings call
  `ensure_system_note(request, ...)` (verified via `req.system`), never write
  to `ctx` (asserted via `not hasattr(ctx, "system")`); response-phase
  findings append formatted strings to `ctx.grounding_issues`; both persist to
  session cache via `append_session_assertion_event` (fire-and-forget,
  scheduled via `asyncio.create_task`); no session ID skips persistence but
  still nudges; `session_snapshot` correctly carries prior events into
  `rule.evaluate()`.
- Real-pipeline registration tests confirm (via `load_config()` +
  `build_request_pipeline`/`build_response_pipeline`, not a hand-built local
  `Pipeline`): `state_assertion_request` appears after `intent_classifier`;
  `state_assertion_response` appears after `grounding_validator` and before
  `model_feedback`.
