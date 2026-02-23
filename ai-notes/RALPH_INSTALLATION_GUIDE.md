# Ralph para Claude Code - Guia de Instalacion

> Guia para instalar y configurar Ralph en cualquier proyecto.
> Basada en la implementacion exitosa en `claude-code-arq-pricing` (Looker SPs pipeline).

## Que es Ralph

Ralph es un **orquestador bash** que ejecuta Claude Code en loop autonomo. Le das un plan con checkboxes, Ralph llama a Claude, Claude ejecuta tareas, marca checkboxes, y Ralph verifica progreso. Si se atora, un circuit breaker lo detiene.

**Resultado real**: Completo 19 tareas en 4 fases, 2 loops, 5 API calls, $0.38 USD.

## Prerequisitos

- Claude Code CLI instalado (`npm install -g @anthropic-ai/claude-code`)
- tmux instalado (`brew install tmux`)
- bash 4+ (`brew install bash` en macOS)

---

## Paso 1: Instalar Ralph (global, una sola vez)

```bash
# Clonar el repo oficial
git clone https://github.com/frankbria/ralph-claude-code.git /tmp/ralph-install
cd /tmp/ralph-install

# Ejecutar instalador
./install.sh
```

Esto crea:
```
~/.ralph/
  ralph_loop.sh          # Orquestador principal (el loop)
  ralph_enable.sh        # Wizard para habilitar en proyectos existentes
  ralph_monitor.sh       # Monitor de progreso
  setup.sh               # Crear proyectos nuevos desde cero
  lib/                   # Librerias (circuit_breaker, response_analyzer, etc.)
  templates/             # Templates base (PROMPT.md, AGENT.md, fix_plan.md, .ralphrc)
~/.local/bin/ralph       # Comando global (wrapper a ralph_loop.sh)
```

Verificar instalacion:
```bash
which ralph
# Debe mostrar: /Users/<tu-user>/.local/bin/ralph
```

---

## Paso 2: Habilitar Ralph en tu proyecto

### Opcion A: Wizard interactivo (recomendado)

```bash
cd /ruta/a/tu-proyecto
ralph enable
```

El wizard:
1. Detecta tipo de proyecto (Python, TypeScript, etc.)
2. Crea `.ralph/` con estructura completa
3. Genera `.ralphrc` con configuracion por defecto
4. Crea templates iniciales

### Opcion B: Manual (control total)

```bash
cd /ruta/a/tu-proyecto

# Crear estructura
mkdir -p .ralph/{specs,hooks,prompts,logs,docs/generated}
```

---

## Paso 3: Configurar .ralphrc

El archivo `.ralphrc` va en la raiz del proyecto. Controla el comportamiento del loop.

```bash
# .ralphrc - Configuracion de Ralph para este proyecto
PROJECT_NAME="mi-proyecto"
PROJECT_TYPE="sql"  # o "python", "typescript", "generic"

# Loop
MAX_CALLS_PER_HOUR=50
CLAUDE_TIMEOUT_MINUTES=10
CLAUDE_OUTPUT_FORMAT="json"

# System prompt del agente (inyectado via --append-system-prompt)
RALPH_SYSTEM_PROMPT=".ralph/claude-ralph.md"

# Herramientas permitidas (sin Bash = mas seguro)
ALLOWED_TOOLS="Write,Read,Edit,Glob,Grep"

# Sesiones (mantiene contexto entre loops)
SESSION_CONTINUITY=true
SESSION_EXPIRY_HOURS=4

# Circuit breaker (detiene el loop si se atora)
CB_NO_PROGRESS_THRESHOLD=3    # 3 loops sin cambios = stop
CB_SAME_ERROR_THRESHOLD=3     # 3 loops con mismo error = stop
```

**Configuraciones segun caso de uso**:

| Caso | ALLOWED_TOOLS | TIMEOUT | MAX_CALLS |
|------|---------------|---------|-----------|
| Solo archivos SQL/MD | `Write,Read,Edit,Glob,Grep` | 10 | 50 |
| Python con tests | `Write,Read,Edit,Bash(pytest),Bash(git *)` | 15 | 100 |
| TypeScript full | `Write,Read,Edit,Bash(npm *),Bash(git *)` | 15 | 100 |

---

## Paso 4: Crear la identidad del agente

Archivo: `.ralph/claude-ralph.md`

```markdown
# Ralph — Agente Automatizado

## Identidad
Eres Ralph, un agente de Claude Code ejecutando tareas automatizadas.
Trabajas de manera autonoma siguiendo un plan estricto. No pides confirmacion, ejecutas.

## Archivos Semanticos (Obligatorios)
ANTES de hacer cualquier cosa, lee estos 3 archivos en orden:
1. `.ralph/fix_plan.md` — Tu plan con checkboxes. Marca [x] al completar cada tarea.
2. `.ralph/specs/ai_learning.md` — Tu memoria de hallazgos y decisiones. Agrega descubrimientos.
3. `.ralph/specs/schema_reference.md` — Referencia tecnica (solo lectura).

## Reglas de Operacion
1. Lee fix_plan.md completo antes de empezar
2. Identifica la primera tarea pendiente [ ]
3. Ejecuta UN solo paso a la vez
4. Despues de cada paso:
   - Marca [x] en fix_plan.md
   - Si descubriste algo nuevo, agregalo a ai_learning.md
5. NUNCA ejecutes queries contra bases de datos
6. NUNCA ejecutes scripts — solo modifica codigo fuente
7. NUNCA uses Bash para modificar archivos — usa Edit/Write
8. Si algo no esta claro, documentalo en ai_learning.md como pregunta abierta y continua
9. No borres logica existente a menos que el plan lo indique

## Flujo de Trabajo
Leer fix_plan.md → Encontrar primera tarea [ ] → Leer ai_learning.md →
Ejecutar tarea → Marcar [x] → Agregar hallazgos → Siguiente tarea

## Reporte de Status
Cuando completes TODAS las tareas de la fase actual, output este bloque:

---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: <numero>
FILES_MODIFIED: <numero>
TESTS_STATUS: NOT_RUN
WORK_TYPE: IMPLEMENTATION
EXIT_SIGNAL: true
RECOMMENDATION: <resumen de lo completado>
---END_RALPH_STATUS---

Si aun hay tareas pendientes, usa EXIT_SIGNAL: false.
```

---

## Paso 5: Crear el plan de tareas

Archivo: `.ralph/fix_plan.md`

```markdown
# Fix Plan: [Nombre del proyecto]

## Fase 0: [Nombre]
- [ ] 0.1 [Tarea especifica con archivo y lineas exactas]
- [ ] 0.2 [Tarea especifica]

## Fase 1: [Nombre]
- [ ] 1.1 [Tarea]
- [ ] 1.2 [Tarea]

## Validacion Final
- [ ] [Check 1]
- [ ] [Check 2]
```

**Tips para tareas efectivas**:
- Incluir rutas de archivo exactas
- Incluir numeros de linea cuando sea posible
- Una accion por checkbox (no "modifica A, B y C")
- Cada fase debe ser independiente de la siguiente

---

## Paso 6: Crear prompts por fase

Archivo: `.ralph/prompts/fase-0.md`

```markdown
# Fase 0: [Nombre]

## Tus 3 archivos semanticos
1. `.ralph/fix_plan.md` — Marca [x] cada tarea que completes.
2. `.ralph/specs/ai_learning.md` — Agrega hallazgos aqui.
3. `.ralph/specs/schema_reference.md` — Referencia (solo lectura).

## Pre-requisito
Verifica en fix_plan.md que no hay fases previas pendientes.

## Tareas de esta fase

### 0.1: [Tarea especifica]
- Archivo: `ruta/al/archivo.py`
- Lineas: 94-105
- Accion: [Instrucciones precisas]

### 0.2: [Tarea especifica]
- Archivo: `ruta/al/archivo.py`
- Accion: [Instrucciones]

## Despues de CADA tarea
1. Marca [x] en `.ralph/fix_plan.md`
2. Registra cambios en `.ralph/specs/ai_learning.md`

## Cuando termines TODAS las tareas de Fase 0
---RALPH_STATUS---
STATUS: COMPLETE
TASKS_COMPLETED_THIS_LOOP: <numero>
FILES_MODIFIED: <numero>
TESTS_STATUS: NOT_RUN
WORK_TYPE: IMPLEMENTATION
EXIT_SIGNAL: true
RECOMMENDATION: [resumen]
---END_RALPH_STATUS---
```

---

## Paso 7: Crear specs de referencia

### `.ralph/specs/ai_learning.md` (memoria del agente)

```markdown
# AI Learning: [Nombre del Proyecto]

> Ralph DEBE registrar aqui cualquier descubrimiento durante la ejecucion.
> NO borrar entradas previas — solo agregar.

## Hallazgos Iniciales
### H-001: [Titulo]
- **Descubierto**: Analisis inicial
- **Severidad**: CRITICA | ALTA | MEDIA | BAJA
- **Detalle**: [Descripcion]

## Decisiones Tomadas
| ID | Decision | Razon | Fecha |
|----|----------|-------|-------|

## Patrones Observados
[Documentar patrones del codigo]

## Registro de Cambios
| Cambio | Archivo | Lineas | Antes | Despues | Fecha |
|--------|---------|--------|-------|---------|-------|
```

### `.ralph/specs/schema_reference.md` (referencia tecnica)

Llenar con schema de BD, arquitectura, o cualquier informacion de solo lectura que Ralph necesite consultar.

---

## Paso 8: Configurar hook de seguridad (file boundary)

### `.ralph/hooks/validate-file-boundary.sh`

```bash
#!/bin/bash
# Hook: Bloquea ediciones fuera del scope permitido

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('file_path',''))" 2>/dev/null)

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# CONFIGURAR: rutas absolutas de tu proyecto
BASE="/ruta/absoluta/a/tu/proyecto"
ALLOWED_WORK="$BASE/directorio-de-trabajo"
ALLOWED_RALPH="$BASE/.ralph"

if [[ "$FILE_PATH" == "$ALLOWED_WORK"* ]] || [[ "$FILE_PATH" == "$ALLOWED_RALPH"* ]]; then
  exit 0
else
  echo "BLOQUEADO: Solo archivos dentro de directorio-de-trabajo/ y .ralph/"
  echo "Intentaste: $FILE_PATH"
  exit 2
fi
```

```bash
chmod +x .ralph/hooks/validate-file-boundary.sh
```

### Registrar hook en `.claude/settings.json`

Agregar dentro de la seccion `hooks`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "./.ralph/hooks/validate-file-boundary.sh"
          }
        ]
      }
    ]
  }
}
```

---

## Paso 9: Ejecutar Ralph

### Ejecucion por fase

```bash
# Enviar prompt de fase al agente
cd /ruta/a/tu-proyecto
claude --append-system-prompt .ralph/claude-ralph.md \
  --output-format json \
  "Lee .ralph/prompts/fase-0.md y ejecuta"
```

### Ejecucion con loop automatico (Ralph completo)

```bash
cd /ruta/a/tu-proyecto
ralph
```

Esto levanta tmux con 3 paneles:
1. **Loop**: El orquestador ejecutando Claude en loop
2. **Live output**: Streaming de la salida de Claude
3. **Status monitor**: Progreso en tiempo real

### Monitorear progreso

```bash
# En otra terminal
ralph_monitor.sh

# O ver logs directamente
tail -f .ralph/logs/ralph.log
tail -f .ralph/live.log
```

---

## Paso 10: Verificar resultados

```bash
# Ver estado
cat .ralph/status.json
# {"status": "completed", "exit_reason": "plan_complete", "loop_count": 2, ...}

# Ver progreso
cat .ralph/progress.json
# {"status": "completed", "timestamp": "2026-02-17 02:17:31"}

# Ver fix_plan.md — todas las tareas deben estar [x]
cat .ralph/fix_plan.md

# Ver que aprendio Ralph
cat .ralph/specs/ai_learning.md
```

---

## Estructura final del proyecto

```
tu-proyecto/
  .claude/
    settings.json          # Hook de file boundary
  .ralph/
    claude-ralph.md        # Identidad del agente
    AGENT.md               # Build/test commands
    PROMPT.md              # Prompt principal
    fix_plan.md            # Plan con checkboxes [x]
    hooks/
      validate-file-boundary.sh
    prompts/
      fase-0.md
      fase-1.md
      ...
    specs/
      ai_learning.md       # Memoria del agente
      schema_reference.md  # Referencia tecnica
    logs/
      ralph.log            # Log del orquestador
      claude_output_*.log  # Output de cada loop
    status.json            # Estado final
    progress.json          # Progreso
  .ralphrc                 # Configuracion del loop
  directorio-de-trabajo/   # Donde Ralph modifica archivos
```

---

## Referencia rapida de comandos

| Comando | Que hace |
|---------|----------|
| `ralph enable` | Wizard para habilitar Ralph en proyecto existente |
| `ralph` | Ejecutar loop completo (tmux) |
| `ralph_monitor.sh` | Monitor de progreso |
| `cat .ralph/status.json` | Ver estado actual |
| `cat .ralph/fix_plan.md` | Ver tareas completadas |

---

## Troubleshooting

### Circuit breaker se activa
El loop se detuvo por falta de progreso. Revisar:
```bash
cat .ralph/.circuit_breaker_state
cat .ralph/.circuit_breaker_history
```
Solucion: Revisar `ai_learning.md` para ver donde se atoro, ajustar el prompt de la fase, y re-ejecutar.

### Ralph no marca checkboxes
Verificar que `claude-ralph.md` tiene las instrucciones de "Marca [x] en fix_plan.md".

### Hook bloquea ediciones validas
Actualizar `BASE` y `ALLOWED_WORK` en `validate-file-boundary.sh` con las rutas correctas.

### Session expired
Si Ralph pierde contexto, verificar `SESSION_EXPIRY_HOURS` en `.ralphrc`.

---

## Ejemplo real: Looker SPs Pipeline

Proyecto que completo exitosamente con Ralph:

- **Tarea**: Modificar 7 SPs en 4 capas de un pipeline Looker BigQuery
- **Fases**: 5 (0-4) + validacion final
- **Tareas**: 19 checkboxes
- **Resultado**: 2 loops, 5 API calls, $0.38 USD
- **Modelo**: claude-opus-4-5 (trabajo) + claude-haiku-4-5 (clasificacion)
- **Tiempo**: ~2 minutos de ejecucion efectiva

Configuracion usada:
```bash
# .ralphrc
PROJECT_NAME="looker-sps-filtros"
PROJECT_TYPE="sql"
MAX_CALLS_PER_HOUR=50
CLAUDE_TIMEOUT_MINUTES=10
ALLOWED_TOOLS="Write,Read,Edit,Glob,Grep"
SESSION_CONTINUITY=true
SESSION_EXPIRY_HOURS=4
CB_NO_PROGRESS_THRESHOLD=3
CB_SAME_ERROR_THRESHOLD=3
```

---

## Links

- GitHub: https://github.com/frankbria/ralph-claude-code
- Proyecto de referencia: `/Users/jeguzman/Documents/deacero/claude-code-arq-pricing`
