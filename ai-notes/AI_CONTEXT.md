# AI_CONTEXT

## Ticket
- Dummy ticket: validar pipeline del repo ai-tooling (scan → context → plan → reviewed → cloud).

## Objetivo
- Crear templates en `templates/` para estandarizar AI_CONTEXT y AI_PLAN.
- Endurecer el workflow:
  - Local (cc-plan): solo texto, sin tools, output a `ai-notes/AI_PLAN.md`.
  - Cloud (cc-agent-cloud): bloquear si el plan no está revisado.

## Inputs
- Archivo de smoke test: /tmp/cc_smoke.py
- Scan existe: ai-notes/cc_smoke.py.analysis.md
- Scripts a modificar/validar:
  - scripts/cc-plan
  - scripts/cc-agent-cloud

## Outputs esperados (en ai-notes/)
- ai-notes/AI_PLAN.md (generado por cc-plan con STATUS: DRAFT)
- (después de revisión humana) ai-notes/AI_PLAN.md con:
  - STATUS: REVIEWED
  - Reviewed-by: <nombre>

## Comandos permitidos (solo estos)
- cc-scan <path>
- cc-plan
- cc-agent-cloud
- cat <file>, sed -n '<a>,<b>p' <file>, grep -n <pattern> <file>, ls -la
- bash -n <script>

## Guardrails (core)
- No ejecutar tools/agent desde cc-plan.
- No inventar comandos/paths/archivos: solo lo listado aquí.
- Outputs SIEMPRE a ai-notes/ (nada de dump en chat).

## Definición de “Reviewed”
- Un plan está revisado si `ai-notes/AI_PLAN.md` contiene:
  - STATUS: REVIEWED (o legacy REVIEWED: YES)
  - Reviewed-by: <nombre>

## Checkpoints / Validación
- `bash -n scripts/cc-plan` y `bash -n scripts/cc-agent-cloud` debe regresar 0.
- `cc-plan` debe generar `ai-notes/AI_PLAN.md` sin alucinar paths/comandos.
- `cc-agent-cloud` debe bloquear si falta plan o no está revisado.

## Validación concreta (Definition of Done)
- Sintaxis OK:
  - bash -n scripts/cc-plan
  - bash -n scripts/cc-agent-cloud
- cc-plan genera plan:
  - rm -f ai-notes/AI_PLAN.md
  - cc-plan
  - test -f ai-notes/AI_PLAN.md
  - grep -n "STATUS:" ai-notes/AI_PLAN.md
- cc-agent-cloud bloquea si falta review:
  - cat > ai-notes/AI_PLAN.md <<'X'
# AI_PLAN
STATUS: DRAFT
Reviewed-by: juve
X
  - cc-agent-cloud || true
- cc-agent-cloud deja pasar cuando está reviewed:
  - cat > ai-notes/AI_PLAN.md <<'X'
# AI_PLAN
STATUS: REVIEWED
Reviewed-by: juve
X
  - cc-agent-cloud || true
