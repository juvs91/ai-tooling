# ADR-0037: Eager Plan-Mode Tool Loading for Fragile Orchestration Models

**Status:** Accepted
**Date:** 2026-08-02
**Supersedes:** —
**Superseded by:** —

---

## Context

A 7847-line real transcript (`bad_conversation.txt`, project `school-system`,
triaged in `ai-notes/analysis/kimi-school-system-drift-triage-2026-08-02.md`,
formalized in `docs/findings/FINDINGS.md` F-001) documents Kimi K2 writing real
code (README, pyproject.toml, tests, an ADR, several modules) while believing it
was in plan mode, when it never was. Root cause chain, confirmed against the
real code in `intent_classifier.py`/`deferred_tools.py`:

1. `PlanModeEnforcementTransformer` (ADR-0015) is a real, hard gate: it blocks
   Edit/Write/MultiEdit/write-Bash whenever `ctx.plan_mode_active == True`. It
   never failed in this incident — it never evaluated, because
   `ctx.plan_mode_active` never became `True`.
2. `ctx.plan_mode_active` is derived by `IntentClassifierTransformer` from real
   signals (EnterPlanMode/ExitPlanMode history, CC's "Plan mode is active"
   system string, intent-classifier PLAN, session-cache). None fired for this
   conversation.
3. `DeferredToolsTransformer`'s final gate (ADR-0012,
   `llm/transformers/deferred_tools.py` — the `_gate_active` block) strips
   `EnterPlanMode`/`ExitPlanMode` from `request.tools` whenever
   `not ctx.plan_mode_active and ctx.intent not in ("PLAN",)` — which was the
   case for the entire session. So Kimi never had these tools available to
   call.
4. When the user later typed the plain-text instruction "remember you are in
   plan mode", Kimi had no tool to call to make that real, reasoned "user says
   already in plan mode" (`bad_conversation.txt` lines 4298-4336), and
   proceeded to write real code. Separately, ~4000 lines earlier
   (lines 7822-7846) Kimi reasoned "There's an EnterPlanMode tool? Not
   listed... I don't have EnterPlanMode tool" — it never called `ToolSearch`
   even once in the full 7847-line transcript (confirmed by grep), so it never
   discovered the tool was simply deferred, not absent.

The tool the model needed was never unreachable by design — it was
conditionally stripped by a gate that assumes the model can recover from a
missing tool by reasoning about it. Kimi cannot: it treats "tool not in my
list" as "tool does not exist," not as "tool is deferred, query for it." This
matches the documented Kimi K2 drift pattern already tracked in ADR-0009.

This is the cheapest, highest-confidence mitigation in `docs/findings/FINDINGS.md`
F-001 (direction 6): stop conditionally stripping `EnterPlanMode`/`ExitPlanMode`
for models already known to be fragile at this specific reasoning task. It ships
independently of the broader state-assertion framework (ADR-0036/0038/0039/0040)
— no shared code, no ordering dependency.

## Decision

### 1. New predicate: `is_fragile_orchestration_model(model)`

Added to `utils/tool_utils.py`, mirroring the exact existing pattern of
`_load_no_tools_models()`/`is_no_tools_model()` (env-driven, `lru_cache(1)`,
substring match, lowercase) — with one deliberate difference: **the default is
non-empty** (`"kimi"`), because the fragility is already documented evidence,
not a hypothetical needing opt-in configuration the way `NO_TOOLS_MODELS`
does (that env var defaults empty because "no native tool calling" is a
capability fact per-deployment, not a documented failure mode).

```python
FRAGILE_ORCHESTRATION_MODELS env var, CSV, default "kimi"
_load_fragile_orchestration_models() -> frozenset[str]   # lru_cache(1)
is_fragile_orchestration_model(model: str) -> bool
```

This is intentionally a separate predicate from `is_no_tools_model` — the two
are orthogonal facts about a model (native tool-calling capability vs.
documented orchestration-reasoning fragility). Conflating them would silently
change behavior for any deployment that sets `NO_TOOLS_MODELS` for a reason
unrelated to plan-mode drift (e.g. a model with no native tool calling but
otherwise reliable reasoning).

### 2. New Step 4b — unconditional eager guarantee, plus final-gate exemption

Two changes to `DeferredToolsTransformer.transform()`
(`llm/transformers/deferred_tools.py`), both additive:

- **Step 4b (new, right after the existing Step 4 PLAN-phase guarantee):**
  for fragile models, unconditionally add any of `_PLAN_ONLY_TOOLS`
  (`EnterPlanMode`, `ExitPlanMode`) missing from `deferred` — **not** gated on
  `ctx.plan_mode_active`, unlike Step 4. This is necessary because Step 1
  (extraction from CC's `<available-deferred-tools>` system-prompt block) only
  populates `deferred` with what CC chose to send for that turn; some project
  configs only include plan-mode tools once plan mode is genuinely active, in
  which case `deferred` would stay empty for a fragile model in a plain BUILD
  turn and there would be nothing to exempt from stripping. Step 4b guarantees
  the tools are always candidates for injection, independent of what CC sent.
- **Final-gate exemption (`_gate_active`, ADR-0012):** the strip condition
  gains `and not is_fragile_orchestration_model(request.model)`, so Step 4b's
  addition isn't immediately undone by the same function's final gate for the
  common case (`intent != "PLAN"`).

```python
_gate_active = (
    not ctx.plan_mode_active
    and ctx.intent not in ("PLAN",)
    and not is_fragile_orchestration_model(request.model)
)
```

Together, fragile models have `EnterPlanMode`/`ExitPlanMode` present in
`request.tools` from the first turn onward regardless of what CC's system
prompt includes that turn — exactly as F-001 direction 6 specifies ("cargar su
schema completo desde el inicio de sesión en vez de dejarlos como deferred
tools"). This is additive-only: no change to Steps 1-4 (extraction, session
cache, PLAN-phase guarantee), no change to `IntentClassifierTransformer` (zero
new signals, zero new branches in an already dense ~500-line function with 5
precedence-ordered signals — verified by reading the file; out of scope for
this ADR).

### 3. Why this doesn't reopen ADR-0012

ADR-0012 introduced the strip specifically to stop Kimi from spuriously
triggering the Plans-tab UI during BUILD sessions when CC's config already
sends `EnterPlanMode`/`ExitPlanMode` in `request.tools` regardless of intent.
The tradeoff inverts only for fragile models, and only because the incident
demonstrates an asymmetry: Kimi **cannot recover** from a missing tool (it
concludes the tool doesn't exist and never queries for it), but it **can**
coexist with a present-but-unneeded tool (it simply doesn't call it in a BUILD
flow — there is no transcript evidence, in this incident or ADR-0009/ADR-0010,
of Kimi calling a tool merely because it was present in `request.tools`). The
risk this reopens — a fragile model calling `EnterPlanMode` in a pure BUILD
session — is a UX nuisance (a spurious Plans-tab prompt the user can dismiss),
not a silent code-drift failure; it is bounded by
`PlanModeEnforcementTransformer` doing nothing if the model never calls the
tool, and by the model needing to actually emit the `tool_use` block (not just
see the tool exists) to have any effect at all.

### 4. Critical correction, found via a live fire test against the real deployed proxy

The design above (Steps 1-4b + final-gate exemption, all inside
`DeferredToolsTransformer`) checks
`is_fragile_orchestration_model(getattr(request, "model", None))`. This was
verified by 1290 passing unit/integration tests — **and was still a complete
no-op in production** for the common case. Running a real "fire test" prompt
against the live proxy (Kimi K2, `school-system` project) and reading
`docker logs` directly surfaced this line:

```
[deferred-tools] final-gate: stripped 2 plan-only tool(s) (intent=BUILD): EnterPlanMode, ExitPlanMode
...
model_in=claude-sonnet-5 model_out=anthropic/kimi-k2
```

Root cause: `DeferredToolsTransformer` runs at position 6 in
`build_request_pipeline` (`proxy/proxy.py`); `ModelRouterTransformer` runs
**last**, at position 10 — and `ModelRouterTransformer` is the *only* place
`request.model` gets rewritten from the client-sent alias (`claude-sonnet-5`,
what Claude Code actually sends) to the real routed target
(`anthropic/kimi-k2`, confirmed via `llm/transformers/model_router.py`'s
several `request.model = ...` assignments). Every test in this ADR constructed
its mock request with `model="kimi-k2"` set directly — none exercised the
real alias-then-routing ordering, so none could have caught this. The check
inside `DeferredToolsTransformer` always evaluated against the pre-routing
alias, never matched `"kimi"`, and the entire Step 4b / gate-exemption
mechanism silently never fired for the realistic case of a client requesting
a model alias that gets routed to a fragile target.

**Fix:** a new transformer, `FragileModelPlanToolsTransformer`
(`llm/transformers/fragile_model_plan_tools.py`), registered in
`build_request_pipeline` immediately **after** `ModelRouterTransformer` —
the only point in the pipeline where `request.model` is guaranteed final and
correct. It re-applies the identical guarantee (ensure `EnterPlanMode`/
`ExitPlanMode` present in `request.tools` for fragile models) using the
now-correctly-resolved model. `DeferredToolsTransformer`'s own Step 4b/gate
exemption is left in place unchanged — it still correctly covers the rarer
case where a client requests the fragile model by its real name directly (no
alias remap needed) — but the new transformer is the one that matters for,
and fixes, the common alias-routed case.

Verified: a regression test
(`tests/test_fragile_model_plan_tools.py::TestOrderingBugRegression::test_alias_then_routing_then_recovery`)
reproduces the exact live scenario — `DeferredToolsTransformer` runs against
the alias first (confirms the bug's premise: tools absent), `request.model`
is then reassigned to the routed target (simulating `ModelRouterTransformer`),
and `FragileModelPlanToolsTransformer` recovers the tools. A structural test
(`TestRealPipelineOrdering`) confirms, via the real `build_request_pipeline()`
— not a hand-built local `Pipeline` — that `fragile_model_plan_tools` runs
after both `deferred_tools` and `model_router`. Confirmed live against the
actual deployed container (`docker exec ... uv run python`, hot-reloaded):
`build_request_pipeline`'s transformer order ends
`..., 'model_router', 'fragile_model_plan_tools'`.

## Explicitly Out of Scope

- Any change to `intent_classifier.py`'s signal logic — this ADR only reads
  `ctx.plan_mode_active`/`ctx.intent`, already computed upstream.
- The general state-assertion mechanism (deferred-tool denial detection,
  no-progress loop guard, exploration grounding, orientation checkpoints) —
  covered by ADR-0036/0038/0039/0040. This ADR ships and is verifiable
  independently of that work.
- Extending eager-loading to other deferred tools (`AskUserQuestion`,
  `WebFetch`, etc.) — only `_PLAN_ONLY_TOOLS` (`EnterPlanMode`, `ExitPlanMode`)
  are in scope, because those are the only tools implicated in this incident's
  plan-mode failure mode. `_PLAN_DEFAULT_TOOLS` and general MCP tools are
  covered by the deferred-tool-denial rule in ADR-0038 instead (a nudge, not
  eager loading, since MCP tool catalogs are dynamic per-session and can't be
  hardcoded the way `_PLAN_ONLY_TOOLS` can).

## Consequences

- Eliminates 100% of the observed occurrences of the plan-mode failure mode in
  the source incident: a fragile model always has `EnterPlanMode`/
  `ExitPlanMode` available to call, so it can never again reason "I don't have
  that tool."
- Zero behavior change for non-fragile models (Claude native, and any
  non-Claude model not matched by `FRAGILE_ORCHESTRATION_MODELS`) — ADR-0012's
  strip applies exactly as before.
- Residual, accepted limitation: this does not make the model reliably *call*
  `EnterPlanMode` when it should — it only ensures the tool is never
  unreachable. A model that still doesn't recognize it needs to enter plan
  mode (e.g. purely from a textual user assertion, with no tool call at all)
  is not fixed by this ADR alone — that gap is the "never accept a textual
  state assertion without verification" principle, addressed generally by the
  `plan_state` rule in ADR-0036/0038.

## Files Changed

- `vendor/claude-code-proxy/utils/tool_utils.py` — new
  `_load_fragile_orchestration_models()` / `is_fragile_orchestration_model()`.
- `vendor/claude-code-proxy/llm/transformers/deferred_tools.py` — `_gate_active`
  condition extended with the new predicate.
- `vendor/claude-code-proxy/tests/test_tool_utils.py` — new tests for the
  predicate (env unset → only "kimi" default matches; env set → CSV parsing;
  case-insensitivity).
- `vendor/claude-code-proxy/tests/test_deferred_tools.py` — new tests: fragile
  model + non-plan intent → `EnterPlanMode`/`ExitPlanMode` NOT stripped;
  non-fragile model + non-plan intent → stripped (ADR-0012 regression);
  fragile model + plan_mode_active=True → unaffected (already kept today).
- `vendor/claude-code-proxy/llm/transformers/fragile_model_plan_tools.py` —
  new (§4 correction): `FragileModelPlanToolsTransformer`, the post-routing fix.
- `vendor/claude-code-proxy/llm/transformers/__init__.py` — export the new
  transformer.
- `vendor/claude-code-proxy/proxy/proxy.py` — register
  `FragileModelPlanToolsTransformer()` immediately after
  `ModelRouterTransformer(...)` in `build_request_pipeline`.
- `vendor/claude-code-proxy/tests/test_fragile_model_plan_tools.py` — new:
  unit tests for the transformer in isolation, the ordering-bug regression
  test reproducing the live scenario, and a real-pipeline ordering check.

## Verification

- Full proxy suite run after implementation, confirming no regressions
  (1298 passed after the §4 correction, up from 1290; +8 tests).
- New unit tests listed above, all green.
- Fixture from `bad_conversation.txt` lines 7822-7846: reconstructed minimal
  request (`model="kimi-k2-...", intent="BUILD", plan_mode_active=False`) run
  through `DeferredToolsTransformer` ⇒ `EnterPlanMode` and `ExitPlanMode`
  present in `request.tools` (the reasoning "I don't have EnterPlanMode tool"
  is no longer possible because the premise is false).
- §4 correction verified against the real deployed container, not just local
  tests: `docker exec ai-tooling-proxy_cloud-1 uv run python -c "..."` (after
  hot-reload) confirms `build_request_pipeline`'s real transformer order ends
  `..., 'model_router', 'fragile_model_plan_tools'`.
