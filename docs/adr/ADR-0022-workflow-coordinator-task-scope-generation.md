# ADR-0022: workflow-coordinator genera task-scope.json al inicio de cada sesión

## Status
Accepted — 2026-07-15

## Context

El sistema de task-modes (`scope-gate.sh` con campos `mode: analysis|build|synthesize|validate`)
requiere que exista un `.claude/task-scope.json` en el proyecto para activarse. Sin él, el hook
hace exit 0 y no hay enforcement.

El problema: hasta ahora ese archivo debía crearse manualmente (Option B) o el agente debía
recordar crearlo como primer tool call (Option A — frágil con modelos de instruction-following débil
como Kimi K2). Los tests del 2026-07-15 demostraron que sin el archivo, Kimi creó 5 docs fuera de
scope sin que nada lo bloqueara.

`workflow-coordinator` ya clasifica el intent del usuario en la primera respuesta de cada sesión.
Es el lugar natural para escribir `task-scope.json` como side-effect del routing.

## Decision

`workflow-coordinator` escribe `.claude/task-scope.json` inmediatamente después de detectar el
intent del usuario, antes de cargar el skill de destino.

**Mapeo intent → mode:**

| Intent detectado | mode | Sufijo de lenguaje |
|---|---|---|
| Análisis, exploración, "cuántos", "qué hace", "cómo funciona" | `analysis` | según contexto del proyecto |
| Implementación, "crea", "fix", "build", "agrega" | `build` | `:ts` si Next.js/React, `:py` si Python, etc. |
| Documentación, "documenta", "escribe docs", "crea guía" | `synthesize` | (ninguno) |
| Verificación, "revisa", "valida", "corre tests" | `validate` | (ninguno) |
| Planeación, "planea", "diseña", "propón" | `full` | (ninguno — entra plan mode) |
| Ambiguo / no clasificable | `full` | (ninguno — sin restricción) |

**Campos generados:**
```json
{
  "task_id": "<intent-slug>-<YYYY-MM-DD>",
  "mode": "<mode>[:<lang>]",
  "allowed_patterns": [],
  "completion_checklist": []
}
```

El `completion_checklist` se deja vacío — es responsabilidad del usuario o del skill específico
poblarlo si necesita verificación granular.

**Self-exception preservada:** `scope-gate.sh` siempre permite escribir `.claude/task-scope.json`
sin importar el modo activo. Esto significa que el agente puede actualizar el scope mid-session
cuando el usuario cambia de tarea (e.g., "analiza X" → "ahora implementa Y").

**Granularidad:** una vez por sesión. La excepción en CLAUDE.md ("si ya hay un skill activo,
omite workflow-coordinator") asegura que solo el primer mensaje define el scope. Mensajes
subsecuentes usan el mismo `task-scope.json` o el agente lo actualiza explícitamente.

## Consequences

**Positivo:**
- Option A funciona sin depender de instruction-following del modelo — workflow-coordinator
  siempre se ejecuta en el primer mensaje y tiene herramientas de Write
- Elimina la fricción de Option B (escribir el archivo manualmente antes de cada sesión)
- No duplica clasificación de intent — workflow-coordinator ya lo hace, solo agrega el side-effect
- Sin conflicto con plan-mode-gate.sh ni otros UserPromptSubmit hooks (capas distintas)

**Negativo / limitaciones:**
- Sesiones multi-tarea requieren que el agente actualice task-scope.json mid-session (manageable)
- La inferencia basada en keywords puede equivocarse en prompts ambiguos → cae a `full` (safe)
- Si workflow-coordinator falla o no carga (sesión sin skill-autoload), no hay task-scope.json
  automático → fallback a behavior actual (sin restricción, igual que antes)
