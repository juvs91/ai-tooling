# Findings Ledger

Discovery entries produced by the software-archeologist and other analysis agents.
**Rule**: Append an F-XXX entry here BEFORE writing to `docs/knowledge/` or opening an ADR.

| ID | Date | Title | Agent | Status |
|----|------|-------|-------|--------|
| F-001 | 2026-08-02 | Kimi K2 no conecta contexto ya explorado con su siguiente acción de tool — falla de orientación en espacios de búsqueda amplios | claude (main session) | confirmed |

---

## F-001: Kimi K2 no conecta contexto ya explorado con su siguiente acción de tool — falla de orientación en espacios de búsqueda amplios

- **Date**: 2026-08-02
- **Agent**: claude (main session, con subagentes Explore para forense de transcript + verificación de código real)
- **Trigger**: Triage de una sesión real y muy mala de Kimi K2.8 en el proyecto
  `school-system` (`bad_conversation.txt`, 7847 líneas). Ver reporte completo:
  `ai-notes/analysis/kimi-school-system-drift-triage-2026-08-02.md`.
- **Summary**: Kimi no falla tanto generando o razonando código — falla
  decidiendo qué hacer cuando tiene que orquestar un espacio de búsqueda amplio
  (leer repo, fetch web, sintetizar lo ya explorado, decidir la siguiente tool,
  actuar de forma congruente con lo aprendido). Evidencia concreta en el
  transcript: (1) Kimi leyó y fetcheó un tutorial completo que explica
  `ToolSearch`/deferred tools, y ~4000 líneas después, necesitando
  `EnterPlanMode` (también un deferred tool), nunca conectó ese conocimiento
  con la acción — de hecho, nunca invocó `ToolSearch(...)` ni una sola vez en
  toda la sesión (confirmado por grep exacto); (2) un loop de ~2800 líneas
  confundiendo nombres de tool (Playwright vs BigQuery vs Serper) sin cambiar
  de estrategia; (3) aceptó una afirmación textual del usuario ("remember you
  are in plan mode") como si fuera el estado técnico real, sin verificar ni
  invocar la tool correspondiente, lo que llevó a implementar código real
  creyendo estar en plan mode. No es un problema de memoria/contexto perdido
  (el contenido relevante seguía presente, sin compresión de por medio) — es
  una limitación de razonamiento/atención del modelo que no conecta
  espontáneamente contexto lejano-pero-presente con la decisión inmediata.
- **Files analyzed**: `bad_conversation.txt` (transcript completo),
  `vendor/claude-code-proxy/llm/transformers/plan_mode_guard.py`,
  `vendor/claude-code-proxy/llm/transformers/intent_classifier.py`,
  `vendor/claude-code-proxy/llm/transformers/grounding_validator.py`,
  `vendor/claude-code-proxy/llm/transformers/quality_refinement.py`,
  `docs/adr/ADR-0009-kimi-model-drift-guardrails.md`,
  `docs/adr/ADR-0031-completion-claim-grounding.md`,
  `docs/adr/ADR-0033-generality-claim-grounding.md`.
- **Direcciones de solución propuestas** (para que el ADR las evalúe, no las
  decide este finding):
  1. Nudge determinístico: cuando el modelo declare textualmente "no tengo la
     tool X" para una tool que el proxy sabe que existe (diferida), inyectar
     automáticamente `"X es un deferred tool — usa ToolSearch('select:X')"`.
  2. Generalizar la heurística de "loop de re-intento sin progreso" de
     ADR-0009 (hoy limitada a `Read`) a cualquier familia de tool
     (`mcp__X__*`, etc.).
  3. Checkeo de grounding "exploration-before-action": verificar que el modelo
     citó/usó contenido de exploración previa en su razonamiento inmediato
     anterior antes de actuar sobre él — no solo que la exploración ocurrió en
     algún punto de la conversación (gap ya señalado en ADR-0033: "evidence
     check is binary and topic-blind").
  4. Checkpoint de "orientación" forzado cada N tool calls en fases de
     exploración amplia, exigiendo síntesis explícita antes de la siguiente
     acción.
  5. Nunca aceptar aserciones textuales de estado (ej. "estás en plan mode")
     sin verificación técnica — aplica tanto a plan-mode como a este patrón
     más general.
- **Linked ADR**: ADR-0036 (state-assertion verification framework — core),
  ADR-0037 (eager plan-mode tool loading for fragile models — direction 6/5,
  ships independently), ADR-0038 (deferred-tool denial rule — direction 1),
  ADR-0039 (no-progress rule — direction 2, reuses existing
  `guardrail.py` detections rather than duplicating them), ADR-0040
  (exploration-grounding rule — direction 3; direction 4 found already covered
  by `intent_classifier.py`'s Override D + `intent_enforcement.py`'s PLAN MODE
  NUDGE, no new rule shipped for it). Related: ADR-0009, ADR-0010, ADR-0012,
  ADR-0015, ADR-0016, ADR-0031, ADR-0033.
- **Disposition per direction**:
  1. Deferred-tool denial nudge → **ADR-0038**, shipped.
  2. Generalize loop-guard beyond Read → **already shipped** pre-existing
     (`guardrail.py`'s `_detect_consistently_failing_tools`/
     `_detect_stuck_tool_calls`, already tool-family-agnostic); ADR-0039 wires
     it into the framework's audit trail rather than reimplementing it.
  3. Exploration-before-action grounding → **ADR-0040**, shipped
     (`exploration_grounding` rule).
  4. Forced orientation checkpoint → **already shipped** pre-existing
     (Override D + PLAN MODE NUDGE); ADR-0040 documents this, no new rule.
  5. Never accept textual state assertions without verification → the
     guiding principle of **ADR-0036**'s framework design itself, not a
     standalone rule.
  6. Cheap fix — don't defer Enter/ExitPlanMode for fragile models →
     **ADR-0037**, shipped independently, first.
- **Linked knowledge**: `ai-notes/analysis/kimi-school-system-drift-triage-2026-08-02.md`,
  `docs/knowledge/kimi-tool-orchestration-context.md`
- **Status**: confirmed

<!-- Template for new entries:

## F-001: <Title>

- **Date**: YYYY-MM-DD
- **Agent**: software-archeologist | retro-engineer | ...
- **Trigger**: <what prompted this finding>
- **Summary**: <1-3 sentences>
- **Files analyzed**: <list of key files>
- **Linked ADR**: ADR-XXXX (if applicable)
- **Linked knowledge**: `docs/knowledge/<file>.md` (if applicable)
- **Status**: draft | confirmed | superseded

-->
