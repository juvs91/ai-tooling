# Plan — Convención de planes fechados en `ai-notes/plans/` (portada desde ai_by_design)

Estado: ejecutado (2026-10-29)

## Context

`ai_by_design/CLAUDE.md` tiene una sección `## Planes` (commit 7e5df94):
- todo plan va en `ai-notes/plans/AAAA-MM-DD-<tema>.md`
- primera línea tras el título = `Estado:` (borrador / ejecutado / descartado, con fecha)
- no se ejecuta un plan sin "implementa" explícito

Allá es solo texto (no hay hook ni `plansDirectory`). En ai-tooling no existe esa regla y todo apunta a `.claude/plans/`. ADR-0028 documenta que plan mode de CC escribe su borrador en `~/.claude/plans/<nombre-aleatorio>.md` y que `ExitPlanMode` lo busca ahí. Por eso **no** se configura `plansDirectory`: el borrador de CC se queda donde está y la copia canónica fechada va a `ai-notes/plans/`.

## Cambios

### 1. `CLAUDE.md`
a) Nueva sección justo después de "Plan Mode — Triggers Obligatorios":
```
## Planes — dónde y con qué formato (MANDATORY)
- Todo plan va en `ai-notes/plans/` y el nombre del archivo empieza con la fecha de creación:
  `AAAA-MM-DD-<tema>.md` (ej. `2026-10-29-planes-fechados-ai-notes.md`).
- La primera línea después del título es `Estado:` (borrador, ejecutado o descartado, con fecha).
  Al ejecutar o descartar el plan, actualiza esa línea y anótalo en `ai-notes/AI_LEARNING.md`.
- Plan mode de Claude Code escribe su borrador en `~/.claude/plans/<nombre-aleatorio>.md` (ADR-0028).
  No lo muevas: antes de `ExitPlanMode`, copia el mismo contenido a `ai-notes/plans/AAAA-MM-DD-<tema>.md`
  con `Estado: borrador (fecha)`.
- Ningún plan se ejecuta sin que el usuario diga "implementa" (o algo igual de explícito).
```
b) Línea 277 (modo analysis, dentro del bloque universal "Task Mode Protocol" que se copia a otros repos): `.claude/plans/` → `ai-notes/plans/`.

### 2. `.claude/hooks/intent-bootstrap.sh` (líneas 89-95)
Agregar `WRITE_PATHS+=("${d}/plans")` en el loop de docs dirs, junto a findings y analysis, y actualizar el comentario de la línea 90. `.claude/plans` se queda (legacy). Con esto el modo analysis puede escribir `ai-notes/plans/`.

### 3. `.claude/hooks/scope-gate.sh`
a) Fallback de analysis (líneas 81-83): `.claude/plans/*|ai-notes/plans/*|findings/*|notes/*` + `ALLOWED_DISPLAY`.
b) **Hueco que encontré:** en modo `build`/`full` con `allowed_patterns` no vacío, escribir un plan se bloquea si `ai-notes/plans/` no está en los patterns. Arreglo: justo antes del chequeo de `allowed_patterns` (línea ~131, al que solo se llega en build/full), agregar
`case "$RELATIVE" in ai-notes/plans/*|.claude/plans/*) exit 0 ;; esac`.
`validate` sigue bloqueando todo y `synthesize` ya lo permite (`ai-notes/*`).

### 4. `.claude/commands/workflow-coordinator.md:55`
"plan file en `.claude/plans/`" → "plan file en `ai-notes/plans/`". El wrapper está en `.claude/commands/`, no en `.agents/`, así que no le aplica el ADR gate.

### 5. Este mismo plan
Al aprobarse, se guarda como `ai-notes/plans/2026-10-29-planes-fechados-ai-notes.md` (la primera prueba de la convención).

## Validación: no falta nada
Revisé todas las referencias a rutas de planes en `.claude/hooks`, `scripts`, `tools`, `.claude/commands`, `.agents/skills`, `templates` y `.claude/settings.json`. Los únicos puntos de enforcement son los 4 de arriba. Notas:
- `ticket-planner`/`ticket-implementation` usan `ai-specs/changes/` para planes de tickets Jira, que es otra convención; no se toca.
- Ningún test en `tools/tests` cubre `scope-gate`/`intent-bootstrap`, así que la verificación es manual (abajo).
- Ambos hooks son `distributable: true` y ai_by_design tiene `.claude/hooks/`, así que llegan allá con `install-hooks.sh` (no se corre en este plan; es seguimiento).

## Fuera de alcance
- Migrar `.claude/plans/observer-llm-*.md`: se quedan como están (según tu comentario).
- Un hook que bloquee nombres sin fecha en `ai-notes/plans/` (posible seguimiento).

## Verificación
- `bash -n` sobre ambos hooks.
- scope-gate con un scope file temporal (JSON por stdin, patrón de CLAUDE.md):
  - analysis con `analysis_write_paths` generado por intent-bootstrap → `ai-notes/plans/x.md` da exit 0 y `src/x.py` exit 2
  - analysis sin `analysis_write_paths` (fallback) → `ai-notes/plans/x.md` exit 0
  - build con `allowed_patterns: ["scripts/*"]` → `ai-notes/plans/x.md` exit 0 y `tools/x.py` exit 2
  - validate → `ai-notes/plans/x.md` exit 2
- `pytest tools/tests -q` sin regresiones.
- `./scripts/task-verify.sh`.
