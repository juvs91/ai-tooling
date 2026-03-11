# Fase 0: Configuración Inicial y Validación

## Tus 3 archivos semanticos
Lee estos archivos ANTES de hacer cualquier cambio:
1. `.ralph/fix_plan.md` — Estado de tareas. Marca [x] cada tarea que completes.
2. `.ralph/specs/ai_learning.md` — Hallazgos previos. Agrega nuevos descubrimientos aqui.
3. `.ralph/specs/schema_reference.md` — Referencia de dominio.

## Pre-requisito
Verifica en fix_plan.md que las fases anteriores estan completas (todas [x]).
Si no estan completas, terminalas primero.

## REGLA DE ORO 🚨

**OBLIGATORIO**: Ejecuta herramientas directamente. NUNCA describas acciones en texto natural.

**CRÍTICO**: Tu comportamiento determina si Ralph puede completar tareas o fallar por generar texto en lugar de ejecutar herramientas.

## PATRONES PROHIBIDOS ❌

Estas acciones ESTÁN PROHIBIDAS. NUNCA las ejecutes:

❌ **PROHIBIDO**: "Voy a listar los archivos..." → NO describir, ejecutar `Glob` inmediatamente
❌ **PROHIBIDO**: "Ahora voy a leer el reporte..." → NO describir, ejecutar `Read` inmediatamente
❌ **PROHIBIDO**: "Luego actualizaré el archivo..." → NO describir, ejecutar `Edit/Write` inmediatamente
❌ **PROHIBIDO**: "Extraeré la información..." → NO describir, ejecutar herramienta inmediatamente
❌ **PROHIBIDO**: "Voy a ejecutar el test..." → NO describir, ejecutar `Bash` inmediatamente
❌ **PROHIBIDO**: Generar texto explicativo sin ejecutar herramienta → **CIRCUIT BREAKER SE ACTIVARÁ**

## PATRONES OBLIGATORIOS ✅

Estas acciones son OBLIGATORIAS. SIEMPRE ejecútalas así:

✅ **OBLIGATORIO**: Ejecutar herramienta inmediatamente sin texto previo
✅ **OBLIGATORIO**: Una herramienta por acción (una herramienta → resultado → siguiente herramienta)
✅ **OBLIGATORIO**: Si necesitas información → Ejecutar herramienta de lectura (`Read`, `Glob`, `Grep`)
✅ **OBLIGATORIO**: Si necesitas modificar → Ejecutar herramienta de escritura (`Edit`, `Write`)
✅ **OBLIGATORIO**: Si necesitas ejecutar comando → Ejecutar `Bash`
✅ **OBLIGATORIO**: Si la herramienta falla → Intentar otra vez con ajustes, NO generar texto de explicación

## DETECCIÓN DE ERRORES Y CORRECCIÓN INMEDIATA

**Si detectas que estás generando texto en lugar de ejecutar herramientas**:

1. **ALERTA INMEDIATAMENTE**: "DETECTADO: Generando texto en lugar de ejecutar herramienta"
2. **CORREGIR INMEDIATAMENTE**: Dejar de generar texto y ejecutar la herramienta correspondiente
3. **DOCUMENTAR EN ai_learning.md**: Agregar nota sobre patrón incorrecto detectado y corregido

**Síntomas de error**:
- Erescribes oraciones como "Voy a...", "Ahora voy a...", "Luego..."
- Generas párrafos explicativos sin tool calls
- El modelo interpreta que no estás progresando → circuit breaker se activa

## Tareas de esta fase

### 0.1: Verificar Configuración de Proxy
- Archivo: `vendor/claude-code-proxy/`
- Descripción: Verificar que el proxy está funcionando correctamente
- Instrucciones detalladas:
  - Ejecutar `curl http://127.0.0.1:8083/health | jq .`
  - Verificar que "status" es "healthy"
  - Verificar que "hot_reload" es true
  - Si no está funcionando, revisar logs en `vendor/claude-code-proxy/`

### 0.2: Validar Integración de Transformers
- Archivo: `vendor/claude-code-proxy/llm/transformers/`
- Descripción: Verificar que todos los transformers AGNOSTIC están creados y registrados
- Instrucciones detalladas:
  - Listar archivos en `vendor/claude-code-proxy/llm/transformers/`
  - Verificar que existen:
    - `universal_tool_extraction.py`
    - `reasoning_handling.py`
    - `model_feedback.py`
    - `quality_refinement.py`
    - `stream_event.py`
  - Verificar que están registrados en `__init__.py`
  - Documentar cualquier transformer faltante en `ai_learning.md`

### 0.3: Verificar Response Pipeline en server.py
- Archivo: `vendor/claude-code-proxy/server.py`
- Descripción: Verificar que el response pipeline AGNOSTIC está integrado
- Instrucciones detalladas:
  - Buscar `build_response_pipeline()` en server.py
  - Verificar que se ejecuta en LiteLLM streaming (líneas ~367-380)
  - Verificar que se ejecuta en LiteLLM non-streaming (líneas ~421-440)
  - Verificar que los 5 transformers están en el pipeline
  - Documentar cualquier integración faltante en `ai_learning.md`

### 0.4: Correr Tests de Validación
- Archivo: `vendor/claude-code-proxy/tests/`
- Descripción: Ejecutar tests para validar la implementación
- Instrucciones detalladas:
  - Ejecutar `python -m pytest tests/test_passthrough_xml_tool.py -v`
  - Verificar que 13/13 tests pasan
  - Ejecutar `python -m pytest tests/ -v --tb=line 2>&1 | head -20`
  - Verificar que >900 tests pasan
  - Documentar cualquier test fallando en `ai_learning.md`

## Despues de CADA tarea
1. Marca [x] la tarea en `.ralph/fix_plan.md`
2. Si descubriste algo, agregalo a `.ralph/specs/ai_learning.md`

## Restricciones
- Solo modifica archivos dentro de `vendor/claude-code-proxy/`
- No ejecutes scripts destructivos
- Usa Edit/Write para modificar archivos, NO Bash
- Si algo falla, documentalo en ai_learning.md y continua con la siguiente tarea

## Cuando termines TODAS las tareas de esta fase
Output este bloque EXACTO (reemplaza los valores):
```
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: 4
FILES_MODIFIED: 0
TESTS_STATUS: PASSED
WORK_TYPE: VALIDATION
EXIT_SIGNAL: true
RECOMMENDATION: Fase 0 completa. Configuración y validación exitosa. Continuar con Fase 1.
---END_RALPH_STATUS---
```