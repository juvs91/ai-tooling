# ADR-0047: Hook `SessionStart` para sincronización diaria de skills y hooks en proyectos hijos

- **Estado:** Aceptado
- **Fecha:** 2026-08-04
- **Autor:** jeguzman

---

## Contexto

Al auditar `school-system` contra `ai-tooling` (ver `ai-notes/analysis/kimi-fire-test-verification-2026-08-03.md`
para el contexto que originó esa auditoría) se encontró drift real: skills desactualizadas,
2 skills obsoletas que ya no existen en `ai-tooling`, y hooks con mejoras de seguridad reales
(`adr-gate.sh`) nunca portadas. La corrección fue manual esta vez — la pregunta de fondo es
cómo evitar que el drift se acumule otra vez sin intervención.

**Alternativas descartadas:**
- **Symlinks** (`.claude/skills -> ../.agents/skills` o similar): ya se probó como piloto y se
  rechazó explícitamente en ADR-0043 el mismo día — rompen al clonar en otra máquina, el
  discovery queda sin documentar, y ningún repo hijo puede pinnear/divergir una versión de un
  skill a propósito. El mismo razonamiento aplica aquí, no solo a skills individuales.
- **Git sparse-checkout**: resuelve qué parte del árbol de UN repo materializar (así lo usa
  `gitops-monorepo` dentro de `ai-tooling` para ramas/worktrees del mismo repo). `ai-tooling` y
  un proyecto como `school-system` son dos repos con historias independientes — traer
  `.agents/skills/` de uno a otro por sparse-checkout requeriría un remote cruzado (submodule/
  subtree), lo que añade complejidad de git real sin resolver el problema de fondo: seguiría
  haciendo falta un `fetch`/`pull` periódico, el mismo gap de "¿cuándo se actualiza?" que ya
  tiene la copia actual.

**Lo que ya existía y no había que reconstruir**: `.agents/sync_skills.sh` ya trae un throttle
de 24h (`.agents/.last_sync`) y resolución de conflictos vía `soul.md` — el problema no era la
lógica de sync, era que nada la ejecutaba automáticamente. `scripts/install-hooks.sh` (copia
hooks/scripts marcados `# distributable: true`, regenera solo la clave `.hooks` de
`settings.local.json`) tampoco necesitaba lógica nueva, solo ejecución periódica.

---

## Decisión

Un hook `SessionStart`, `auto-sync-daily.sh`, distribuible (se propaga a proyectos hijos igual
que el resto de hooks), que **ejecuta el sync real** en cada sesión, no solo lo advierte:

1. **Skills**: llama directamente a `.agents/sync_skills.sh` del proyecto consumidor. No
   reimplementa nada — el script ya decide si ya sincronizó hoy (su propio throttle) y ya
   resuelve dónde va cada skill.
2. **Hooks/scripts**: llama a `scripts/install-hooks.sh` del repo fuente (resuelto vía
   `local_path` en el marker `.ai-tooling`), con un throttle **nuevo** propio
   (`.claude/.last_hook_sync`, 24h) — `install-hooks.sh` no traía uno y reescribe
   `settings.local.json` en cada corrida.
3. Ninguno de los dos borra nada — skills obsoletas y hooks eliminados en `ai-tooling` siguen
   requiriendo un paso manual, igual que hoy.
4. Si el proyecto no tiene marker `.ai-tooling` (nunca se adoptó el sistema), el hook es un
   no-op silencioso — no es un error, es la señal de que este mecanismo no aplica ahí.

### Corrección necesaria en `install-hooks.sh` para que esto funcione

Dos problemas reales encontrados al implementar el hook, no cosméticos:

1. **`install-hooks.sh` solo agrupaba 3 eventos hardcodeados** (`PreToolUse`, `PostToolUse`,
   `UserPromptSubmit`). Un hook nuevo con `# event: SessionStart` se habría copiado
   igual (la copia es genérica) pero **nunca habría quedado registrado en
   `settings.local.json`** — exactamente la misma clase de bug de "gate instalado pero muerto
   en silencio" que ya se documentó para el método de append de `tools/install_hooks.sh` sobre
   el pre-commit de git (ver el plan de sync de `school-system`). Se generalizó el
   agrupamiento para descubrir el nombre del evento dinámicamente (un archivo temporal por
   evento en vez de tres variables fijas) — agregar un cuarto, quinto o enésimo tipo de evento
   (`Stop`, `Notification`, `PreCompact`, etc.) a futuro no requiere tocar este script de nuevo.
2. **Riesgo de auto-modificación**: este hook, al correr `install-hooks.sh` sobre el propio
   proyecto donde se está ejecutando, sobrescribe `.claude/hooks/auto-sync-daily.sh` — el mismo
   archivo que el shell tiene abierto en ese momento. `cp` directo trunca el archivo de destino
   en sitio, lo que puede corromper la lectura si el intérprete no había buffereado ya todo el
   script. Se cambió `copy_distributable()` a `cp` hacia un archivo temporal seguido de `mv`
   (rename atómico dentro del mismo filesystem) — un descriptor de archivo ya abierto sigue
   apuntando al inode viejo hasta que el proceso termina, sin importar qué le pase al nombre en
   el directorio.

### Lo que NO cambia

- Sin symlinks, sin sparse-checkout cruzado entre repos.
- `sync_skills.sh` sigue sin podar — 2 skills obsoletas por proyecto (encontradas en
  `school-system`) requieren `rm` manual la primera vez que se detectan.
- El formato de `.ai-tooling` (marker) no cambia — el hook solo lee un campo que ya existía
  (`local_path`).

---

## Consecuencias

### Positivas
- El drift que motivó esta ronda de trabajo (skills desactualizadas, hooks de seguridad no
  portados) no debería volver a acumularse silenciosamente por más de 24h en ningún proyecto
  que adopte el marcador `.ai-tooling` con `local_path`.
- La generalización de eventos en `install-hooks.sh` es reutilizable para cualquier hook futuro
  sin volver a tocar el script.
- El fix de `cp`+`mv` atómico elimina una clase de bug (auto-corrupción de script en ejecución)
  que aplica a cualquier hook distribuible que en el futuro decida invocar `install-hooks.sh`
  sobre sí mismo, no solo a este.

### Negativas / Costos
- Cada sesión nueva paga el costo de un `git rev-parse` + lectura de 2 archivos de throttle,
  aunque sea no-op el resto del día — mitigado con `async: true` (no bloquea la sesión) y
  timeout de 30s.
- Un proyecto sin `local_path` en su `.ai-tooling` (por ejemplo el propio `ai-tooling`, que es
  la fuente y no un consumidor) no sincroniza hooks — comportamiento esperado, no un bug.

### Neutrales
- Las skills/hooks obsoletos detectados manualmente en `school-system` durante esta ronda
  (`agentic-agile`, `agentic-agile-work-reporter`) no se vuelven a podar automáticamente por
  este hook — sigue siendo intencional, ver "Lo que NO cambia".

---

## Implementación

Archivos modificados/creados:
- `.claude/hooks/auto-sync-daily.sh` — nuevo, distribuible, `event: SessionStart`, `async: true`.
- `scripts/install-hooks.sh` — agrupamiento de eventos generalizado (dinámico, no 3
  hardcodeados); `copy_distributable()` usa `cp` a temporal + `mv` atómico.
- `scripts/gitops-init.sh`, `scripts/release.sh` — se les agregó `# distributable: true` en esta
  misma ronda (gap encontrado en la misma auditoría: ninguno se distribuía, dejando
  desactualizado en `school-system` el formato unificado de `adr-gate.conf` — ver ADR-0035 — y
  el subsistema de versionado por manifiesto de `release.sh`).

Se distribuye a proyectos hijos (incluyendo `school-system`) la próxima vez que se corra
`scripts/install-hooks.sh` manualmente, o automáticamente en su primer `SessionStart` una vez
que el hook mismo llegue ahí por esa misma vía.
