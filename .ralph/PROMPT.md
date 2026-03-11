# {{PROJECT_NAME}} — {{OBJECTIVE_TITLE}}

## Tus 3 Archivos Semanticos
SIEMPRE lee estos archivos antes de hacer cualquier cambio:

| Archivo | Proposito | Tu accion |
|---------|-----------|-----------|
| `.ralph/fix_plan.md` | Tracking de tareas | Marca [x] al completar cada tarea |
| `.ralph/specs/ai_learning.md` | Hallazgos y decisiones | Agrega descubrimientos, documenta decisiones |
| `.ralph/specs/schema_reference.md` | Referencia de dominio | Solo lectura — consulta para referencia |

## Objetivo
{{OBJECTIVE_DESCRIPTION}}

## Directorio de Trabajo
UNICO directorio para modificar: `{{WORKING_DIRECTORY}}/`

## Reglas
1. Solo modificar archivos dentro de `{{WORKING_DIRECTORY}}/`
2. No ejecutar queries ni comandos destructivos
3. No borrar logica existente a menos que el plan lo indique
4. Documentar cada cambio en `.ralph/specs/ai_learning.md`
5. Marcar [x] en `.ralph/fix_plan.md` despues de cada tarea

## Flujo de Trabajo
1. Lee fix_plan.md → encuentra primera tarea pendiente [ ]
2. Lee ai_learning.md → contexto de hallazgos previos
3. Ejecuta la tarea
4. Marca [x] en fix_plan.md
5. Si descubriste algo → agrega a ai_learning.md
6. Siguiente tarea

## Criterios de Exito
{{SUCCESS_CRITERIA}}

## Reporte de Status
Cuando completes TODAS las tareas de la fase actual, output este bloque:
```
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: <numero>
FILES_MODIFIED: <numero>
TESTS_STATUS: NOT_RUN
WORK_TYPE: IMPLEMENTATION
EXIT_SIGNAL: true
RECOMMENDATION: <resumen de lo completado>
---END_RALPH_STATUS---
```
Si aun hay tareas pendientes, usa EXIT_SIGNAL: false.
