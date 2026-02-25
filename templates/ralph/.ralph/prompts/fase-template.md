# Fase {{N}}: {{FASE_TITLE}}

## Tus 3 archivos semanticos
Lee estos archivos ANTES de hacer cualquier cambio:
1. `.ralph/fix_plan.md` — Estado de tareas. Marca [x] cada tarea que completes.
2. `.ralph/specs/ai_learning.md` — Hallazgos previos. Agrega nuevos descubrimientos aqui.
3. `.ralph/specs/schema_reference.md` — Referencia de dominio.

## Pre-requisito
Verifica en fix_plan.md que las fases anteriores estan completas (todas [x]).
Si no estan completas, terminalas primero.

## Tareas de esta fase

### {{N}}.1: {{TASK_TITLE}}
- Archivo: `{{FILE_PATH}}`
- Descripcion: {{TASK_DESCRIPTION}}
- Instrucciones detalladas:
  {{DETAILED_INSTRUCTIONS}}

### {{N}}.2: {{TASK_TITLE}}
- Archivo: `{{FILE_PATH}}`
- Descripcion: {{TASK_DESCRIPTION}}

## Despues de CADA tarea
1. Marca [x] la tarea en `.ralph/fix_plan.md`
2. Si descubriste algo, agregalo a `.ralph/specs/ai_learning.md`

## Restricciones
- Solo modifica archivos dentro de `{{WORKING_DIRECTORY}}/`
- {{PROJECT_SPECIFIC_CONSTRAINTS}}

## Cuando termines TODAS las tareas de esta fase
Output este bloque EXACTO (reemplaza los valores):
```
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: <numero>
FILES_MODIFIED: <numero>
TESTS_STATUS: NOT_RUN
WORK_TYPE: IMPLEMENTATION
EXIT_SIGNAL: true
RECOMMENDATION: Fase {{N}} completa. Continuar con Fase {{N+1}}.
---END_RALPH_STATUS---
```
