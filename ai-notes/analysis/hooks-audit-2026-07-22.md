# Auditoría de hooks — ai-tooling (2026-07-22)

Pedido: analizar los 19 hooks en `.claude/hooks/` en busca de bugs/tareas pendientes, y listar
todos los hooks y mejoras metidas hasta ahora. Se leyó el contenido completo de los 19 archivos
(no solo grep) y se cruzó contra `settings.json` y `CLAUDE.md`.

## Bugs / hallazgos reales encontrados

### 1. `plan-mode-gate.sh` tiene el MISMO patrón vulnerable que ya arreglamos para skill-load-gate.sh

`plan-mode-gate.sh:13` respeta un bypass file `.claude/no-plan-gate` (mismo patrón de diseño que
`.claude/no-skill-gate`) — pero a diferencia de `skill-load-gate.sh`, **no tiene protección**:
ni el archivo `.claude/no-plan-gate` ni `plan-mode-gate.sh` mismo están cubiertos por
`protect-skill-gate-bypass.sh` (que solo protege los 2 nombres del gate de skills). El agente
podría crear `.claude/no-plan-gate` para saltarse el mandato de Plan Mode, exactamente como pasó
con `.claude/no-skill-gate` en school-system.

**No confirmado como incidente real** (verificado: `.claude/no-plan-gate` no existe hoy en
ninguno de los 3 repos) — es un hallazgo preventivo, no una explotación ya ocurrida.

**Acción sugerida**: extender `protect-skill-gate-bypass.sh` (renombrándolo a algo más genérico,
ej. `protect-gate-bypass.sh`) para cubrir también `no-plan-gate` + `plan-mode-gate.sh`, mismo
patrón exacto.

### 2. `quality-gate.sh` existe como archivo pero NO está registrado en ai-tooling ni school-system

`CLAUDE.md` (ai-tooling) documenta `quality-gate.sh` en su tabla de hooks como activo
("PostToolUse | Edit|Write | Corre `ruff check` en `.py` modificados") — pero
`.claude/settings.json` de ai-tooling **no lo registra en absoluto** (confirmado: 0 ocurrencias
del comando exacto `quality-gate.sh` fuera del substring dentro de `ts-quality-gate.sh`). Mismo
caso en school-system: el archivo existe en `.claude/hooks/quality-gate.sh` pero no aparece en su
`settings.json`. Solo wpc-backend lo tiene correctamente registrado
(`Edit|Write|MultiEdit` → `quality-gate.sh` + `migration-gate.sh` + `verify-implementation.sh`).

Es código muerto en 2 de los 3 repos, documentado como si estuviera activo. Dado que ai-tooling
sí tiene código Python (`vendor/claude-code-proxy/`), el hook sería relevante ahí si se
registrara.

**Acción sugerida**: registrar `quality-gate.sh` en `settings.json` de ai-tooling y
school-system (mismo bloque que wpc-backend), o si la intención fue deprecarlo a favor de otro
mecanismo, quitar la fila de `CLAUDE.md` y borrar el archivo huérfano.

### 3. `skill-autoload.sh` puede saltarse el mandato "SIEMPRE cargar workflow-coordinator primero"

`CLAUDE.md` dice: *"Tu PRIMER tool call en cada respuesta DEBE ser el Skill tool con
skill='workflow-coordinator'... Excepción: Si ya hay un skill activo en el contexto de esta
sesión, omite este paso."*

`skill-autoload.sh:46-49`: si `.claude/task-scope.json` YA existe (creado por
`intent-bootstrap.sh`, que corre ANTES en el mismo array de `UserPromptSubmit` hooks), el hook
sale sin emitir el recordatorio "MANDATORY: Call Skill tool", con el comentario *"intent is
classified... workflow-coordinator adds no value — skip to avoid routing overhead"*.

Como `intent-bootstrap.sh` crea `task-scope.json` en el PRIMER prompt de toda sesión (si hay
`session_id`+`prompt`), esto significa que **el primer prompt de cada sesión nunca recibe el
recordatorio obligatorio** — la clasificación heurística de `intent-bootstrap.sh` (regex bash)
no es lo mismo que el routing real de `workflow-coordinator` (tabla en `AGENTS.md`), pero el
código las trata como equivalentes.

**No está claro si es un bug o una decisión de diseño deliberada** (evitar doble trabajo) que
quedó sin reconciliar con el texto de `CLAUDE.md`. Se deja como hallazgo para decisión del
usuario, no se asume cuál es la intención correcta.

## Hooks sin bugs encontrados (revisados completos, sin hallazgos)

`adr-gate.sh`, `edit-drift-detector.sh`, `intent-bootstrap.sh`, `migration-gate.sh`,
`protect-secrets.sh`, `scope-gate.sh`, `task-scope-updater.sh`, `verify-implementation.sh`,
`worktree-isolation-gate.sh`, `config-protection.sh`.

## Inventario completo: 19 hooks activos en ai-tooling (fuente canónica)

| Hook | Evento | Matcher | Qué hace |
|------|--------|---------|----------|
| `block-dangerous.sh` | PreToolUse | (any) | Bloquea `rm -rf` en worktree activo, `git push --force`, `git reset --hard`, etc. |
| `adr-gate.sh` | PreToolUse | Edit\|Write | Bloquea edits a rutas guardadas sin ADR staged |
| `worktree-isolation-gate.sh` | PreToolUse | Workflow\|Agent | Advierte `parallel(agent())` sin `isolation: 'worktree'` |
| `skill-load-gate.sh` | PreToolUse | Agent\|EnterPlanMode | Bloquea si no se leyó ningún SKILL.md en la sesión |
| `protect-skill-gate-bypass.sh` | PreToolUse | Write\|Edit\|Bash | Bloquea que el agente cree `.claude/no-skill-gate` o edite `skill-load-gate.sh` |
| `config-protection.sh` | PreToolUse | Edit\|Write\|MultiEdit | Bloquea edits a `pyproject.toml` (secciones tool.*), `ruff.toml`, `.eslintrc*`, `.prettierrc*`, `.pre-commit-config.yaml` |
| `protect-secrets.sh` | PreToolUse | Edit\|Write\|MultiEdit | Bloquea escribir secrets en archivos trackeados por git |
| `quality-enforce.sh` | PreToolUse | Edit\|Write | Bloquea edits a un archivo específico con errores TS pendientes (por archivo, no por proyecto) |
| `scope-gate.sh` | PreToolUse | Edit\|Write | Bloquea edits fuera del scope de `task-scope.json` (analysis/validate/synthesize/build) |
| `track-skill-load.sh` | PostToolUse | * | Marca la sesión cuando cualquier tool referencia un SKILL.md |
| `ts-quality-gate.sh` | PostToolUse | Edit\|Write | Corre `tsc` tras edits TS, guarda estado por archivo |
| `migration-gate.sh` | PostToolUse | Edit\|Write\|MultiEdit | Avisa si se edita un modelo SQLAlchemy sin correr Alembic |
| `verify-implementation.sh` | PostToolUse | Edit\|Write\|MultiEdit | Detecta funciones stub (`pass`/`...`/TODO) en `.py` editados |
| `edit-drift-detector.sh` | PostToolUse | Edit\|Write\|Bash | Advierte a 8/15/25 edits sin correr tests; resetea contador al detectar test run |
| `sync_skills.sh` | UserPromptSubmit | (any) | Sync de skills (throttle 24h) |
| `intent-bootstrap.sh` | UserPromptSubmit | (any) | Crea `task-scope.json` con modo/checklist detectado por regex del prompt |
| `skill-autoload.sh` | UserPromptSubmit | (any) | Recuerda cargar `workflow-coordinator` (ver hallazgo #3) |
| `task-scope-updater.sh` | UserPromptSubmit | (any) | Detecta cambio de modo mid-sesión y actualiza `task-scope.json` |
| `plan-mode-gate.sh` | UserPromptSubmit | (any) | Inyecta mandato de Plan Mode una vez por sesión (ver hallazgo #1) |
| `quality-gate.sh` | *(archivo existe, NO registrado — ver hallazgo #2)* | — | Ruff check async en `.py` modificados |

## Mejoras/fixes metidos esta sesión (cronológico)

1. **wpc-backend ADR gate**: agregado patrón guardado `apis/*/app/**/*.py` (antes solo cubría
   `.agents/skills/`) + fix de YAML folding en `.pre-commit-config.yaml`.
2. **`check_adr_gate.py`** (ai-tooling y school-system): parser de `.claude/adr-gate.conf` para
   reglas configurables por proyecto, con fallback a `GUARDED_PATTERNS` hardcoded.
3. **`task-verify.sh`** (ai-tooling y school-system): auto-borra `task-scope.json` al completar
   con éxito (scope queda abierto para la siguiente tarea sin intervención manual) + guard de
   re-entrancia (`TASK_VERIFY_RUNNING`) contra recursión infinita si el propio checklist se
   auto-referencia.
4. **`.gitignore`**: corregido `.worktrees/` → `.claude/worktrees/` (school-system), agregado
   `.claude/worktrees/` (wpc-backend).
5. **`track-skill-load.sh` + `skill-load-gate.sh`** (los 3 repos): nuevo par de hooks — detecta
   lectura de cualquier `SKILL.md` vía CUALQUIER tool (no solo `Read`) y bloquea `Agent`/
   `EnterPlanMode` si no se detectó ninguna en la sesión.
6. **`protect-skill-gate-bypass.sh`** (los 3 repos, nuevo hoy): bloquea que el agente cree
   `.claude/no-skill-gate` o edite `skill-load-gate.sh` — cierra el incidente real donde un
   agente se auto-eximió del gate anterior.
7. **`block-dangerous.sh`** (ai-tooling y school-system): fix de falso positivo — segmenta el
   comando por `&&`/`||`/`;`/`|` antes de chequear `rm -rf` + path de worktree, para no bloquear
   cuando ambos aparecen en segmentos no relacionados del mismo comando.
8. **`quality-enforce.sh` + `ts-quality-gate.sh`** (ai-tooling y school-system): cambio de clave
   de estado de "por proyecto" a "por archivo" — un error TS en un archivo ya no bloquea edits en
   TODO el proyecto, solo en ese archivo específico.
9. **`permissions.deny`** (los 3 repos): agregado `"TaskOutput"` (tool deprecada) — reemplaza un
   diseño de hook custom por el mecanismo nativo de permisos.
10. **`workflow-coordinator.md`** (los 3 repos): ToolSearch obligatorio en cualquier modo (no solo
    planning), aclaración de scope de `context7`, guía anti-degradación (no intentar tools sin
    relación cuando un hook bloquea), bullet mecánico de verificación de la tabla de routing.
11. **Proxy (`vendor/claude-code-proxy/`, solo ai-tooling)**:
    - `llm/schemas.py`: `Tool.input_schema` opcional + campo `type` + `extra="allow"` — soporta
      tools server-side de Anthropic sin perder datos.
    - `utils/schema_utils.py`: `is_server_tool()` para detectarlas.
    - `llm/converters.py`: filtra tools server-side antes de convertir a formato OpenAI/litellm
      (ADR-0029) + fix de import circular (reordenado tras `_system_to_text`).
    - `router/model_mapper.py`: `KNOWN_PREFIXES` ampliado con `deepseek/`/`groq/` — arregla bug
      real de doble-prefijo de modelo.
    - `llm/transformers/intent_classifier.py`: Signal 4 (desbloqueo implícito de plan-mode)
      ahora también infiere el origen desde el historial de mensajes, no solo desde cache de
      sesión.
    - `router/llm_router.py`: `STRONG_PLANNING_RE` — el empate PLAN/BUILD en
      `_regex_fallback_intent` ya no lo gana cualquier palabra de `PLANNING_RE`, solo una señal
      inequívoca de "quiero un plan".
    - Suite de tests del proxy: de `93 failed, 1098 passed` a `1191 passed, 0 failed`.
12. **`ai-notes/AI_LEARNING.md`** (ai-tooling y school-system): documentadas las entradas P007-P009
    (ai-tooling) y la sesión "Fix Pre-existing Quality Issues" completa con los 2 bugs de
    infraestructura que generó (school-system).

## Qué NO se revisó y por qué

- No se auditaron los hooks de `.ralph/hooks/` en wpc-backend (fuera del pedido — el pedido fue
  sobre "hooks" en general pero el contexto de la sesión es ai-tooling; se puede extender si se
  pide explícitamente).
- No se re-verificó el comportamiento de los hooks de wpc-backend/school-system contra bugs
  nuevos — solo se confirmó presencia/ausencia de archivos y registro en `settings.json`, no se
  leyó su contenido completo de nuevo (ya se hizo en sesiones anteriores para los que se tocaron).
- El `SessionStart`/`Stop` hooks de wpc-backend (bootstrap agent, telemetry reporter) no se
  auditaron — son específicos de ese repo y no se tocaron esta sesión.
