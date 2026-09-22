# ADR-0058: Gate SYNTHESIZING intent on prior read history

- **Date**: 2026-09-21
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

The intent classifier (`IntentClassifierTransformer` in `llm/transformers/intent_classifier.py`) supports a `SYNTHESIZING` intent, documented in its own docstring as "a sub-phase of READ (triggered after many consecutive reads)". The LLM classifier, however, can emit `SYNTHESIZING` for a plain summary request with no analysis session in progress — observed in production with Kimi Code CLI: a 4-turn conversation about SSE refactoring (zero tool calls, `history=none reads=0` in the classify log) where the user asked "Resume en 5 puntos...". The regex fallback disagreed (`DISAGREE: llm=SYNTHESIZING regex=CHAT`) but the LLM intent wins routing.

That single misclassification set off the whole analysis pipeline on a non-analysis request: the guardrail injected the synthesis prompt, the quality gate scored the first pass 0.40 and triggered a non-streaming refinement, and the refinement produced an empty response (ADR-0057 documents the fail-safe added for the delivery side). The root cause chain starts here, at the classifier: a phase that by design requires prior read turns was granted without a single one.

## Decision

1. **Demote `SYNTHESIZING` when there is no read history.** After classification and before phase mapping, if `ctx.intent == "SYNTHESIZING"` and `consecutive_reads == 0`, replace the intent with `_regex_fallback_intent(last_text)`. The regex detector cannot return SYNTHESIZING (only READ/BUILD/PLAN/VERIFY/CHAT), so the demoted request flows through the normal, non-analysis path.

2. **Read turns in the conversation as the gate signal.** The gate uses the raw `_count_consecutive_reads(messages)` — not the analysis-gated `consecutive_reads` variable, which is zeroed when analysis keywords fall outside the history scan window (`_detect_analysis_from_history` scans at most 10 user messages). The phase precondition is the existence of read turns, and that count is computed directly from assistant tool_use blocks. Zero read turns is a definitive violation of the phase precondition ("triggered after many consecutive reads") — no heuristics or thresholds involved.

3. **Legitimate SYNTHESIZING is unchanged.** When an analysis session is genuinely in progress (`consecutive_reads > 0`), the intent is kept and phase mapping proceeds exactly as before.

4. **No model/provider-specific logic.** The gate inspects only conversation state (read count), never model names or provider quirks. It applies identically to every backend.

## Consequences

**Positive:**
- Closes the root of the ADR-0057 failure chain: summary/synthesis requests outside an analysis session are classified by the deterministic regex, the synthesis prompt is never injected, and the broken refinement path is never entered for them.
- Removes the recurring internal token burn: each previously-misclassified summary wasted a ~24k-input re-request before the fail-safe replayed the original.

**Negative / Trade-offs:**
- If a future legitimate use case needs "synthesize with zero prior reads" (e.g. synthesizing tool results inlined in the prompt), this gate blocks it. That case can be re-enabled deliberately by relaxing the condition with evidence, not by default.
- The gate is a guardrail against the LLM classifier's false positives, not a fix for the classifier's prompt; disagreement metrics (`metrics.increment_classifier_disagreement`) remain the observability signal if the classifier keeps misfiring.

## Files Modified

| File | Change |
|------|--------|
| `vendor/claude-code-proxy/llm/transformers/intent_classifier.py` | Demote `SYNTHESIZING` to regex intent when `consecutive_reads == 0`, after classification and before phase mapping. |
| `vendor/claude-code-proxy/tests/test_intent_classifier.py` (or nearest existing classifier test module) | Tests: demotion without read history; preservation with read history. |
| `docs/adr/ADR-0058-gate-synthesizing-on-read-history.md` | This ADR. |
