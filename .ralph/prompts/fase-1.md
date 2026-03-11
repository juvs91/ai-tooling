# Fase 1: Fix Proxy Fallback Bug

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

❌ **PROHIBIDO**: "Voy a analizar el código..." → NO describir, ejecutar `Read` inmediatamente
❌ **PROHIBIDO**: "Ahora voy a buscar el bug..." → NO describir, ejecutar `Grep` inmediatamente
❌ **PROHIBIDO**: "Luego modificaré el proxy.py..." → NO describir, ejecutar `Edit` inmediatamente
❌ **PROHIBIDO**: "Extraeré la información del fallback..." → NO describir, ejecutar herramienta inmediatamente
❌ **PROHIBIDO**: "Voy a verificar el fix..." → NO describir, ejecutar herramienta inmediatamente
❌ **PROHIBIDO**: Generar texto explicativo sin ejecutar herramienta → **CIRCUIT BREAKER SE ACTIVARÁ**

## PATRONES OBLIGATORIOS ✅

Estas acciones son OBLIGATORIAS. SIEMPRE ejecútalas así:

✅ **OBLIGATORIO**: Ejecutar herramienta inmediatamente sin texto previo
✅ **OBLIGATORIO**: Una herramienta por acción (una herramienta → resultado → siguiente herramienta)
✅ **OBLIGATORIO**: Si necesitas leer código → Ejecutar `Read`
✅ **OBLIGATORIO**: Si necesitas buscar bugs → Ejecutar `Grep`
✅ **OBLIGATORIO**: Si necesitas modificar código → Ejecutar `Edit`
✅ **OBLIGATORIO**: Si necesitas verificar el fix → Ejecutar `Bash` (tests/health check)
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

## Contexto del Bug

**Problema**: Fallback a LiteLLM no preserva el parámetro `tools=` cuando el provider es Anthropic.

**Impacto**:
- ❌ HTTP 500 "All providers failed"
- ❌ Fallback completamente roto
- ⚠️ **MENOS CRÍTICO** con UniversalToolExtractionTransformer (puede extraer tools de output)

**Ubicación**: `vendor/claude-code-proxy/proxy/proxy.py` líneas 390-411 (fallback loop)

## Tareas de esta fase

### 1.1: Leer y Analizar el Código del Fallback
- Archivo: `vendor/claude-code-proxy/proxy/proxy.py`
- Descripción: Analizar el código del fallback loop para entender el bug
- Instrucciones detalladas:
  - Leer líneas 390-411 de proxy.py
  - Identificar donde se cambia el modelo (request_obj.model = ...)
  - Identificar donde se re-evalúa is_no_tools_model()
  - Documentar el flujo exacto del bug en ai_learning.md

### 1.2: Leer la Función convert_anthropic_to_litellm
- Archivo: `vendor/claude-code-proxy/llm/converters.py`
- Descripción: Entender cómo se procesan los tools en la conversión
- Instrucciones detalladas:
  - Buscar la función convert_anthropic_to_litellm
  - Leer la lógica de procesamiento de tools (líneas ~518-534)
  - Entender cuándo se agregan tools y cuándo se eliminan
  - Documentar la lógica en ai_learning.md

### 1.3: Implementar el Fix del Fallback Bug
- Archivo: `vendor/claude-code-proxy/proxy/proxy.py`
- Descripción: Modificar el fallback loop para preservar tools correctamente
- Instrucciones detalladas:
  - **Paso 1**: Antes del loop, guardar estado original: `original_had_tools = bool(anthropic_request.tools)`
  - **Paso 2**: Verificar soporte real del proveedor: `provider_supports_tools = not is_no_tools_model(provider.get_litellm_model(ctx.intent))`
  - **Paso 3**: Solo modificar tools si el proveedor nuevo REALMENTE no los soporta
  - **Paso 4**: Si `original_had_tools` y el nuevo modelo es no-tools, copiar tools del request original
  - Usar Edit para modificar proxy.py líneas 390-411
  - Asegurar que la lógica preserva tools cuando corresponde
  - Documentar los cambios exactos en ai_learning.md

### 1.4: Verificar Sintaxis y Hot-Reload
- Archivo: `vendor/claude-code-proxy/proxy/proxy.py`
- Descripción: Verificar que los cambios no rompen la sintaxis Python
- Instrucciones detalladas:
  - Ejecutar `python -m py_compile vendor/claude-code-proxy/proxy/proxy.py`
  - Verificar que no hay errores de sintaxis
  - Ejecutar `curl http://127.0.0.1:8083/health | jq .` para verificar hot-reload
  - Documentar el resultado en ai_learning.md

### 1.5: Validar que el Fix Funciona
- Archivo: `vendor/claude-code-proxy/`
- Descripción: Validar que el fallback bug está arreglado
- Instrucciones detalladas:
  - Ejecutar tests relevantes del proxy: `python -m pytest tests/test_proxy.py -v -k fallback`
  - Verificar que los tests pasan
  - Si no hay tests específicos de fallback, documentar la necesidad de crearlos en ai_learning.md
  - Documentar el resultado de validación en ai_learning.md

## Despues de CADA tarea
1. Marca [x] la tarea en `.ralph/fix_plan.md`
2. Si descubriste algo, agregalo a `.ralph/specs/ai_learning.md`

## Restricciones
- Solo modifica archivos dentro de `vendor/claude-code-proxy/`
- No ejecutes scripts destructivos
- Usa Edit/Write para modificar archivos, NO Bash
- Si algo falla, documentalo en ai_learning.md y continua con la siguiente tarea
- El fix debe ser AGNOSTIC (no model-specific logic)

## Cuando termines TODAS las tareas de esta fase
Output este bloque EXACTO (reemplaza los valores):
```
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: 5
FILES_MODIFIED: 1
TESTS_STATUS: PASSED
WORK_TYPE: BUGFIX
EXIT_SIGNAL: true
RECOMMENDATION: Fase 1 completa. Proxy fallback bug arreglado. Continuar con Fase 2.
---END_RALPH_STATUS---
```