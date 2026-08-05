# ADR-0041: Task-Scope File Per Session, Not Per Project

**Status:** Accepted
**Date:** 2026-08-03
**Supersedes:** ADR-0022 (parcial — mantiene el mapeo intent→mode, cambia la
resolución de ruta del archivo)
**Superseded by:** —

---

## Context

ADR-0022 estableció que `intent-bootstrap.sh` (sucesor pure-bash de la idea
original de `workflow-coordinator`) escribe `.claude/task-scope.json` al
inicio de cada sesión, y que `scope-gate.sh` lo lee para restringir
escrituras según el `mode` declarado. Ambos, junto con `task-scope-updater.sh`
(actualiza `.mode` mid-sesión) y `scripts/task-verify.sh` (verifica el
`completion_checklist` y borra el scope al completar la tarea), fijan la
misma ruta literal: `$CWD/.claude/task-scope.json`.

Esa ruta es única **por proyecto**, no por sesión. ADR-0022 asumía
implícitamente una sesión de Claude Code a la vez por proyecto — nunca
contempló sesiones concurrentes. En uso real eso ya no se sostiene: se
confirmó en este mismo repo que `.claude/sessions/` contenía marcadores de
3 sesiones distintas mientras un único `.claude/task-scope.json` era
compartido por las tres. Con dos sesiones simultáneas sobre el mismo repo,
la sesión B sobreescribe (`intent-bootstrap.sh`, `task-scope-updater.sh`) o
borra (`task-verify.sh` al completar su propia tarea) el scope que la sesión
A estaba usando activamente — `scope-gate.sh` de A pasa a enforzar el modo
de B sin que A lo pidiera.

El repo ya resuelve el mismo problema de identidad-por-sesión en otros
cuatro hooks no relacionados con task-scope
(`skill-autoload.sh`, `plan-mode-gate.sh`, `track-skill-load.sh`,
`skill-load-gate.sh`, y el propio `BOOTSTRAP_MARKER` de
`intent-bootstrap.sh`): todos sanitizan `session_id` del stdin JSON
(`tr -cd 'a-zA-Z0-9_-'`) y lo usan como sufijo de archivo bajo
`.claude/sessions/${SESSION_ID}-*`, directorio ya gitignored. Ese patrón
nunca se extendió al `task-scope.json` en sí — solo se usó para markers
auxiliares de esos mismos hooks.

`scripts/task-verify.sh` es la única pieza sin stdin JSON de hook (se
invoca directo vía la tool Bash). Se confirmó que el proceso que lo ejecuta
sí expone `CLAUDE_CODE_SESSION_ID` en el entorno, con el mismo valor que el
`session_id` que reciben los hooks de esa sesión — suficiente para darle la
misma identidad sin rediseñar cómo se invoca el script.

## Decision

Introducir `.claude/hooks/task-scope-lib.sh`: una librería bash compartida
(no un hook — sin header `# event:`, así `install-hooks.sh` la distribuye
como archivo plano pero no la registra en `settings.json`) con una única
función, `scope_file_for_session(cwd, raw_session_id)`, que:

- sanitiza `raw_session_id` igual que los hooks existentes,
- si hay session id, devuelve `${cwd}/.claude/sessions/${sid}-task-scope.json`,
- si no hay session id (fallback), devuelve `${cwd}/.claude/task-scope.json`
  — el path legacy, preservado como fallback deliberado, no descontinuado.

Los cuatro consumidores (`intent-bootstrap.sh`, `task-scope-updater.sh`,
`scope-gate.sh`, `scripts/task-verify.sh`) fuentean esta librería y resuelven
`SCOPE_FILE` con ella en vez de usar el literal fijo. Cada uno obtiene su
`session_id` de la fuente que ya tiene disponible: stdin JSON para los tres
hooks, `$CLAUDE_CODE_SESSION_ID` del entorno para `task-verify.sh`. Ningún
consumidor necesita iterar sobre múltiples archivos — cada invocación trae
su propio session id y opera exclusivamente sobre el archivo que le
corresponde.

`task-scope-lib.sh` no reemplaza a `task-scope-updater.sh` ni a ningún hook
existente: es lógica de resolución de ruta, pura y sin estado, extraída para
que las 4 implementaciones no diverjan — la misma razón por la que
ADR-0032 extrajo estado de sesión compartido a `llm/session/*` en el proxy.

**Self-exception de `scope-gate.sh`** (permitir siempre escribir el scope
file, sin importar el modo activo): se preserva, pero comparando contra el
`SCOPE_FILE` ya resuelto de la sesión actual, no contra el literal fijo.

## Alternatives Considered

- **Locking/merge sobre un único archivo compartido** (ej. flock, o merge de
  campos al escribir). Rechazada: agrega complejidad de concurrencia real
  (locks, retries, resolución de conflictos) para un problema que ya tiene
  una solución más simple disponible en el propio repo — namespacing por
  sesión, sin estado compartido que proteger.
- **Mover el session id al nombre del propio proceso o a un directorio por
  PID.** Rechazada: `session_id` ya es la identidad estable que usan los
  otros 4 hooks del repo; introducir una segunda noción de identidad (PID)
  para el mismo problema fragmentaría el patrón en vez de reusarlo.

## Consequences

**Positivo:**
- Sesiones concurrentes sobre el mismo proyecto dejan de pisarse: cada una
  tiene su propio `task-scope.json`, su propio enforcement de `scope-gate.sh`,
  y su propio ciclo de vida (`task-verify.sh` borra solo el suyo).
- Reusa un patrón ya probado en 4 lugares del mismo repo — no introduce un
  mecanismo nuevo de identidad.
- El path legacy (`.claude/task-scope.json`) sigue siendo válido como
  fallback — no hay regresión para contextos sin `session_id` disponible.

**Negativo / limitaciones:**
- Solo se confirmó `CLAUDE_CODE_SESSION_ID` disponible en el entrypoint VS
  Code. Si algún entrypoint (ej. CLI `--print`) no lo expone,
  `task-verify.sh` cae al path legacy fijo para ese caso — mismo
  comportamiento que existía antes de este cambio, no una regresión, pero
  tampoco resuelve el bug para ese entrypoint puntual.
- Los scope files por sesión se acumulan en `.claude/sessions/` sin limpieza
  automática si una sesión se abandona sin correr `task-verify.sh` — mismo
  trade-off ya aceptado hoy para los markers de `bootstrap`/`plan-gate`/
  `skill-loaded` de esos mismos hooks (no hay hook `SessionEnd` en este
  repo). El archivo huérfano es inerte: gitignored, no interfiere con
  otras sesiones.

## Files Changed

- `.claude/hooks/task-scope-lib.sh` (nuevo)
- `.claude/hooks/intent-bootstrap.sh`
- `.claude/hooks/task-scope-updater.sh`
- `.claude/hooks/scope-gate.sh`
- `scripts/task-verify.sh`
- `CLAUDE.md` (sección "Task Mode Protocol")

## Verification

Simular dos sesiones concurrentes con `session_id` sintéticos
(`testA`, `testB`) contra los 4 archivos: bootstraps independientes no se
pisan, `scope-gate.sh` enforza el modo de cada sesión por separado,
`task-verify.sh` invocado con `CLAUDE_CODE_SESSION_ID=testB` borra solo el
scope de `testB` y deja intacto el de `testA`, y sin `session_id` el
fallback cae al path legacy sin romper. Ver `scripts/task-verify.sh` y los
hooks para el detalle exacto de cada invocación de prueba.
