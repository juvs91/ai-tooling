# ADR-0009: KimiK2 Model Drift — MCP Tool Validation & Loop Guardrails

**Status:** Accepted  
**Date:** 2026-06-25  
**Author:** juvs

---

## Context

Se observó drift severo en sesiones de KimiK2 a través del proxy:
- Un archivo leído 40+ veces consecutivas sin avanzar
- `ReadMcpResourceTool` llamado repetidamente con `server="context7"` (falla siempre)
- Bash commands fallidos repetidos sin cambio de estrategia

El análisis reveló cuatro causas raíz combinadas:

1. **Silent tool drop sin feedback:** Cuando el modelo emite un tool call cuyo nombre no está en `valid_names`, el proxy hace `continue` sin generar `tool_result`. El modelo nunca sabe que el tool no existe → loop infinito.

2. **MCP tools no reconocidos como válidos:** `validate_tool_name_with_deferred_bypass` solo bypassea `_CC_WORKFLOW_TOOL_NAMES` (14 herramientas CC). Los MCP tools del usuario (patrón `mcp__server__tool`) son filtrados como "hallucinated" aunque estén configurados en `.mcp.json`.

3. **Guardrail de loop mal calibrado para single-file obsession:** El hard enforcement (drop Read tool) solo dispara cuando ≥3 archivos distintos tienen duplicados. Un modelo que lee 1 archivo 40 veces nunca activa el enforcement — solo recibe una nota de texto ignorada.

4. **Threshold SYNTHESIZING demasiado alto:** El override D (READ → SYNTHESIZING) dispara a los 15 reads consecutivos. KimiK2 puede loopear mucho antes de ese umbral.

## Decision

Implementar las siguientes correcciones en orden de impacto/riesgo:

### 1. Patrón `mcp__*__*` como bypass permanente en validación

Modificar `validate_tool_name_with_deferred_bypass` para reconocer cualquier nombre con ≥2 underscores dobles como un MCP tool legítimo del usuario, sin necesidad de enumerarlos. Los MCP tools siguen el patrón `mcp__<servidor>__<tool>` — este patrón no colisiona con ningún nombre de herramienta CC.

### 2. Ampliar `_CC_WORKFLOW_TOOL_NAMES`

Agregar las herramientas CC presentes en los `<system-reminder>` deferred de sesiones reales pero ausentes del frozenset: `ReadMcpResourceTool`, `ListMcpResourcesTool`, `ReadMcpResourceDirTool`, `Monitor`, `SendMessage`, `PushNotification`, `RemoteTrigger`, `ScheduleWakeup`, `DesignSync`.

### 3. Recalibrar guardrail de duplicate reads

Cambiar la condición `if dup_file_count >= 3` a `if dup_file_count >= 1` para activar el hard enforcement (drop Read tool) cuando CUALQUIER archivo está siendo leído en loop, no solo cuando hay ≥3 archivos distintos afectados. Expandir la ventana de 30 a 50 mensajes.

### 4. Feedback retroactivo para tool calls sin tool_result

Cuando un turno de asistente contiene tool_use blocks que no recibieron tool_result en el siguiente turno de usuario (señal de drop silencioso), inyectar una nota en el system prompt del siguiente request: el modelo debe saber que su tool call no fue ejecutado.

### 5. Configuración SYNTHESIZING threshold (sin código)

Reducir `ANALYSIS_SYNTHESIZE_READS_FALLBACK` de 15 a 6 en los profile envs de kimi.

## Consequences

**Positivo:**
- MCP tools del usuario ya no se dropean silenciosamente → el modelo recibe feedback y puede cambiar de estrategia
- El loop guard activa enforcement antes (1 archivo en loop vs. 3 archivos distintos)
- El feedback retroactivo cierra el ciclo de información para loops ya iniciados
- El threshold SYNTHESIZING más bajo acelera la salida de read loops

**Riesgo controlado:**
- El bypass por patrón `__` es amplio pero deliberado: cualquier herramienta que el usuario configure en `.mcp.json` es legítima por definición
- Ampliar `_CC_WORKFLOW_TOOL_NAMES` puede exponer tools no disponibles en todos los entornos — se mitiga porque el proxy solo válida el nombre, no garantiza disponibilidad (eso lo maneja CC)
- El guardrail más agresivo (>= 1) podría en teoría bloquear el Read tool antes de tiempo en sesiones con un solo archivo legítimamente grande — aceptable porque el modelo siempre puede escribir antes de que el guardrail active

### 6. Detectar `EnterPlanMode` durante BUILD execution con `HAS_WRITES` (Fix 7)

Cuando `ctx.history_phase == "HAS_WRITES"` (el modelo ya editó archivos en la sesión),
inyectar RULE 8 en `_get_building_prompt()` para advertir al modelo que no llame
`EnterPlanMode`. La función actualmente no recibe `ctx`, por lo que se pasa también
`ctx` desde `_get_enforcement_prompt()`. Sin hardcoding: la condición es `HAS_WRITES`
detectado por el clasificador, no el nombre del modelo ni de ningún tool.

### 7. Reducir overhead de tool schemas — `TOOL_EXCLUDE` + `TOOL_SCHEMA_MAX_DESC` (Fix 8)

**Causa raíz confirmada:** 80 tools × ~725 tokens promedio = **57,964 tokens** fijos por
request en Kimi K2 (131K window), dejando solo 74K para conversación. Cuando la sesión
crece, el proxy recalcula `remaining~-18083` (negativo), forzando compresión agresiva a 25
mensajes que destruye el contexto.

**Solución dual:**

1. **`TOOL_EXCLUDE`** (blacklist por prefijo) — elimina tools que ningún modelo usa actualmente.
   Implementado en `ToolAllowlistTransformer` como patrones glob (`mcp__playwright__*`).
   Activo en todos los perfiles vía env var. El transformer protege `_CC_WORKFLOW_TOOL_NAMES`
   para que no sean excluidos por accident.

2. **`TOOL_SCHEMA_MAX_DESC=200`** — trunca descriptions de tools y sus properties a 200 chars.
   No elimina tools ni parámetros — solo acorta metadatos verbosos. La función `trim_tool_schemas()`
   vive en `utils/tool_utils.py` (utilería compartida). Reduce overhead ~60%: de 57K a ~20K tokens.

**Archivos modificados:**
- `utils/tool_utils.py` — nueva función `trim_tool_schemas()`
- `config.py` — `PolicyConfig.tool_exclude_raw` + `PolicyConfig.tool_schema_max_desc`
- `llm/transformers/tool_allowlist.py` — `_parse_exclude()`, `_matches_exclude()`, integración al final de `transform()`
- `profile-envs/*.env` (14 archivos) — `TOOL_EXCLUDE=mcp__playwright__*` + `TOOL_SCHEMA_MAX_DESC=200`

**Aplica a todos los modelos** — sin configuración por perfil, sin hardcoding de model names.

### 8. Context amnesia en sesiones largas — RULE 9 + compresión rica con estado de tareas (Fix 9)

**Causa raíz:** En sesiones de 100+ turnos, la compresión elimina mensajes recientes. El modelo
construye `old_string` para Edit/MultiEdit desde memoria comprimida → falla repetidamente.
Además, el último `TodoWrite` suele estar en `recent_messages` (no comprimidos), por lo que
`extract_session_state()` nunca lo ve y el estado de tareas se pierde al comprimir.

**Soluciones implementadas (4 capas):**

1. **RULE 9 [READ-BEFORE-EDIT]** — en `intent_enforcement.py` `_get_building_prompt()`:
   instruye al modelo a leer el archivo antes de construir `old_string` SALVO que ya lo
   haya leído/escrito en el mismo turno. Elimina assumption drift sin penalizar casos claros.

2. **RULE 10 [TASK-STATE-FILE]** — en el mismo building prompt: el modelo escribe su estado
   de tareas a `ai-notes/task-state-YYYYMMDD.md` vía `cat >>` con heredoc. El proxy no puede
   escribir a `ai-notes/` (sin volumen montado en docker-compose), por lo que esta capa la
   ejecuta el modelo directamente.

3. **TodoWrite state en PRESERVED_STATE** — `extract_todo_state(full_messages)` en
   `_apply_preserved_state()`: escanea el historial completo (old + recent) en busca del
   último `TodoWrite`. Los todos extraídos se inyectan como `## Active Tasks` en el bloque
   `--- PRESERVED_STATE ---` del system prompt post-compresión.
   - Nuevo dataclass `TodoItem` en `session_state.py`
   - Campo `todos: list[TodoItem]` en `SessionState` (backward-compatible: `.get("todos", [])`)
   - Condición actualizada: `if not state.todos` incluida en el guard de `inject_state_into_system_prompt()`

4. **Fix 9-C — archivos modificados vs leídos** — `EntityInfo` ahora tiene `modified: bool = False`.
   `extract_session_state()` escanea bloques `tool_use` de Edit/Write/MultiEdit/NotebookEdit
   ANTES de la conversión a texto (los tool_use no tienen campo `"text"`). El PRESERVED_STATE
   separa `## Files Modified` (✏️) de `## Files Read`.

5. **`_COMPRESS_PROMPT` enriquecido** — el summarizer (DeepSeek) recibe instrucciones de
   preservar estado de tareas completadas/pendientes y archivos modificados, y estructura el
   resumen en `## Completed Work → ## Pending Work → ## Files Modified → ## Key Decisions`.

**Archivos modificados:**
- `llm/transformers/intent_enforcement.py` — RULE 9 + RULE 10 como variables separadas después de `anti_plan`
- `llm/session_state.py` — `TodoItem`, `extract_todo_state()`, `todos` en `SessionState`, condición + `## Active Tasks`, `modified` en `EntityInfo`
- `llm/compressor.py` — `full_messages` param en `_apply_preserved_state()`, 2 call sites, `_COMPRESS_PROMPT`

## Alternatives Considered

- **Enumerar todos los MCP tools en el proxy:** Requeriría que el proxy se conecte a cada servidor MCP para descubrir sus tools. Complejidad alta, acoplamiento al runtime de MCP. Descartado a favor del bypass por patrón.
- **Generar `tool_result` sintético en converters.py:** Técnicamente correcto pero complejo — converters.py transforma la respuesta del modelo, no puede inyectar mensajes de usuario. El enfoque de nota en system prompt logra el mismo efecto con menor riesgo.
