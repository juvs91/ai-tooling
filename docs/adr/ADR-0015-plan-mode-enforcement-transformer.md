# ADR-0015: Plan Mode Enforcement Transformer

**Status:** Accepted  
**Date:** 2026-07-13  
**Supersedes:** —  
**Superseded by:** —

---

## Context

Claude Code has an `EnterPlanMode` / `ExitPlanMode` tool pair that enables structured planning before implementation. However, the model frequently skips entering plan mode when the user explicitly requests planning ("planea", "diseña", "qué harías") or when the task complexity warrants it (≥3 files). The result is free-form planning responses without the structured plan file and user-approval gate that plan mode provides.

Existing enforcement:
- `workflow-coordinator` skill has plan mode guards but only when explicitly invoked
- `CLAUDE.md` has no plan mode rules (gap)
- No proxy-level signal exists for plan mode intent detection

## Decision

Add `PlanModeEnforcementTransformer` to the request pipeline in `proxy/proxy.py:build_request_pipeline()`, positioned after `IntentClassifierTransformer` (which sets `ctx.plan_mode_active`).

The transformer:
1. **If plan mode is NOT active** and the last user message matches explicit planning keywords → injects `[PLAN-MODE-REQUIRED]` system note
2. **If plan mode IS active** → injects `[PLAN-MODE-ACTIVE]` exit reminder

Keywords use `re.search` with `\b` word boundaries to avoid false positives (e.g. "planeamos" ≠ "planea"). Only very explicit, unambiguous planning phrases are matched — CLAUDE.md and the hook handle ambient cases.

This is one layer of a three-layer system:
- **Layer A** (Hook): `plan-mode-gate.sh` — UserPromptSubmit, once per session
- **Layer B** (Proxy): This ADR — keyword-triggered system note injection
- **Layer C** (CLAUDE.md): Permanent mandatory language in project instructions

## Consequences

**Positive:**
- Model receives a system-level reminder on every request where planning intent is unambiguous
- `ctx.plan_mode_active` from `IntentClassifierTransformer` gives authoritative signal — no re-derivation
- Composable with existing pipeline; hot-reload applies automatically
- Conservative keyword set minimizes false positives

**Negative:**
- Adds one transformer to the request pipeline (negligible latency)
- Keyword matching cannot catch all natural-language planning requests (by design — conservative)

## Implementation

- New file: `vendor/claude-code-proxy/llm/transformers/plan_mode_enforcement.py`
- Modified: `vendor/claude-code-proxy/proxy/proxy.py` (one import + one line in `Pipeline([...])`)
