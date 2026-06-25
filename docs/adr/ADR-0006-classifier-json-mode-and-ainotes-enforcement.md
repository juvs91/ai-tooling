# ADR-0006: Classifier JSON Mode Enforcement + ai-notes Write Enforcement

**Status**: Accepted  
**Date**: 2026-06-24  
**Context**: Proxy running Kimi K2 (primary) + DeepSeek (classifier + fallback)

## Problem

### 1. Classifier accuracy: 43.9%
The intent classifier calls DeepSeek via `classify_intent()` in `router/llm_router.py` without
`response_format={"type": "json_object"}`. DeepSeek sometimes wraps JSON in markdown code blocks
or adds explanatory text before the JSON object, causing `json.loads()` to fail silently and
fall through to regex classification. The regex classifier has ~44% accuracy on the current
traffic mix (PLAN and READ intents most misclassified).

Previously GLM-4-flash was used as classifier and was more consistent at returning bare JSON
without enforcement. DeepSeek requires explicit JSON mode opt-in.

### 2. ai-notes not being written
The `analysis_enforcements` counter shows 202 enforcement attempts but `analysis_refinements: 0`.
The `IntentEnforcementTransformer` injects system prompts for SYNTHESIZING/BUILD phases but
does not explicitly mandate using the `Write` tool to persist analysis to `ai-notes/`. Models
(Kimi K2 and DeepSeek) generate analysis content in the response stream but never call Write,
so when VS Code restarts the analysis is lost entirely.

## Decision

### Fix 1: JSON mode for DeepSeek classifier
Add `response_format={"type": "json_object"}` to the `kwargs` dict in `classify_intent()`
(`router/llm_router.py`). DeepSeek's API supports this natively. The existing `except` clause
already falls back to regex on any API error, so this is safe to add unconditionally.

### Fix 2: Mandatory ai-notes write in SYNTHESIZING and BUILD prompts
Strengthen `_get_synthesizing_prompt()` and `_get_building_prompt()` in
`llm/transformers/intent_enforcement.py` to include an explicit MANDATORY rule requiring
the model to use the `Write` tool to save analysis/session output to `ai-notes/{name}.md`
before ending the turn. Use strong language ("MANDATORY", "REQUIRED before ExitPlanMode").

## Consequences

- Classifier accuracy expected to improve significantly (DeepSeek JSON mode eliminates format failures)
- ai-notes files will be created during analysis sessions, surviving VS Code restarts
- No new dependencies, no config changes required
- `response_format` is ignored by LiteLLM for models that don't support it (safe)
