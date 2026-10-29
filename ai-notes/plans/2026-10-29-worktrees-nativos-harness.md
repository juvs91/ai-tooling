# Plan: worktrees en el harness (ADR-0073), versión 9 (convergida: la ronda 9 no pidió cambios materiales)

Estado: ejecutado (2026-10-29) — ver ADR-0073. Pendiente: edición humana del skill gate (test W3).

## Contexto
Hubo 6 rondas de validación: la doc oficial, el repo, 8 revisores adversariales, un barrido de completitud y
experimentos con git 2.56 en `/private/tmp`. Los worktrees nativos de Claude (`EnterWorktree`, `--worktree`,
`isolation: 'worktree'`) rompen, o dejan fallando abiertos, hooks y scripts del harness. Además, el estado por
sesión ya está roto con un simple `cd`, y la basura resultante llegó a commits (`vendor/hlwss/.claude/*`).

**Decisión (estable desde la v2):** no crear hooks `WorktreeCreate`/`WorktreeRemove`.
- `WorktreeCreate` reemplaza la creación nativa, y si falla la sesión termina con exit 1.
- Desactiva `.worktreeinclude`.
- Se propagaría a todos los consumidores sin opt-out.

En su lugar: `.worktreeinclude` más hooks y scripts conscientes de los worktrees.

**Hechos confirmados:**
- `CLAUDE_PROJECT_DIR` se queda en la raíz donde arrancó la sesión, y `.cwd` sigue al worktree y a los `cd`.
- El stderr con exit 0 nunca llega al modelo. `additionalContext` en PreToolUse existe, y
  `permissionDecision:"allow"` **auto-aprueba** el tool.
- `CLAUDE_PROJECT_DIR` no existe en el entorno de Bash; `CLAUDE_CODE_SESSION_ID` sí, y el subagente comparte el
  sid del padre.
- `isolation` y `run_in_background` del Agent no están documentados en `tool_input`.
- git imprime siempre paths físicos. Hay doble montaje `/case-sensitive` → `/Volumes/case-sensitive`.
- En submódulos y en `--separate-git-dir`, la primera entrada de `worktree list` **no** es el checkout
  principal.

## Principios
1. **La raíz del estado (`state_root`) ancla en `CLAUDE_PROJECT_DIR`** (respeta los subproyectos) y se mapea
   fuera de un worktree enlazado. La usan los hooks nuevos que tocan `.claude/{sessions,quality-state,
   drift-state}`, los escape hatches y `.claude/.gitignore`. El scope sigue en `project_root`, que apunta al
   mismo directorio físico pero sin canonizar, por compatibilidad.
2. **Las acciones sobre el archivo editado usan el repo del archivo** (`repo_of_file`).
3. **Worktree enlazado ⇔ `--git-dir` ≠ `--git-common-dir`.** Así se clasifican bien los submódulos y
   `--separate-git-dir`. Nada que escriba archivos trackeados corre dentro de un worktree enlazado.
4. **Todo se canoniza (`pwd -P`) antes de comparar o de quitar prefijos.** La excepción es `project_root`; el
   consumidor que compara (`scope-gate`) lo canoniza localmente.
5. **Compatible con consumidores en versiones mixtas**: `declare -F` más un fallback al comportamiento actual.

## Fase 0: preparación y verificación empírica
0. **Requisito previo:** hay cambios sin commitear que **no son de esta sesión** (probablemente de otra sesión
   concurrente): `scope-gate.sh`, `intent-bootstrap.sh`, `CLAUDE.md`, `workflow-coordinator.md`,
   `AI_LEARNING.md` y `ai-notes/plans/`. Antes de tocar nada, preguntarte si se commitean primero o si se
   integran. No se sobrescriben.
1. Crear el stub de `docs/adr/ADR-0073-worktrees-nativos-harness.md` **sin `git add`**. Su cuerpo contiene
   `.agents/skills/`. Como queda sin trackear (`??`), pasa el gate siempre; un `A` staged y luego editado
   quedaría como `AM` y `adr-gate` bloquearía. En la Fase 3, el regex de `adr-gate` pasa a `^(\?\?|A.)`.
   **El ADR no se commitea hasta terminar las ediciones de `SKILL.md` de la Fase 7.**
2. `git rm -r vendor/hlwss/.claude/{drift-state,.gitignore,task-scope.json}` (trackeados; antes, confirmar que
   no son legítimos) y borrar `.claude/hooks/.claude/`.
3. Hook de log temporal en `settings.local.json`, retirado al final. Corre en sesión normal, tras un `cd`,
   tras `EnterWorktree` y en un Agent con `isolation:"worktree"`. Registra `pwd`, `CLAUDE_PROJECT_DIR`, `.cwd`,
   `session_id`, `agent_id` y el `tool_input` de Agent. Además, un `env` desde Bash en la sesión principal y
   en el subagente aislado. Confirma:
   - (a) el cwd del proceso;
   - (b) `CLAUDE_PROJECT_DIR` dentro del worktree;
   - (c) si `isolation` llega en `tool_input`;
   - (d) el sid compartido;
   - (e) de qué `settings.json` salen los hooks;
   - (f) que `additionalContext` sin decisión llega al modelo;
   - (g) **si hay un discriminador de entorno estable entre el hilo principal y un subagente**
     (`CLAUDE_CODE_CHILD_SESSION`, `AI_AGENT`, no documentados);
   - (h) el `.cwd` y `agent_id` en UserPromptSubmit y en el PostToolUse de `EnterWorktree`, tanto en el hilo
     principal como en un subagente aislado.

   Los resultados van al ADR. Solo (g) y (h) cambian una rama del diseño (ver `task-verify`), y las ramas ya
   están definidas. Si (g) es negativo **y** (h) muestra que los subagentes no traen `agent_id` en
   PostToolUse, no se escribe registro y se usa "borrar solo si no es enlazado".

## Fase 1: lib (`.claude/hooks/task-scope-lib.sh`)
Todas las funciones retornan 0 siempre e imprimen vacío si fallan.
- `canon_path <p>`: `CDPATH= cd -P -- <ancestro existente más cercano>` (subiendo con `dirname`) más `pwd -P`
  más el resto del path.
- `git_info <dir>`: un solo fork, `git -C <dir> rev-parse --show-toplevel --path-format=absolute --git-dir
  --git-common-dir`. Validado: 3 líneas estables en todos los layouts, y rc 128 fuera de un repo o dentro de
  `.git`.
  - **Git viejo:** se detecta si la línea 2 empieza con `--` (git < 2.31 hace eco de la opción, no falla). En
    ese caso se compara `canon(dir/gitdir)` con `canon(dir/common)`.
- `is_linked_worktree <dir>`: `gitdir` ≠ `common`.
- `main_worktree_of <dir>`: solo si es enlazado. Toma la primera entrada `worktree` de
  `worktree list --porcelain`, y la usa solo si no es `bare` y si existe `<first>/.git`; si no, devuelve vacío,
  y entonces no se mapea.
- `repo_of_file <file> <cwd>`: resuelve el FILE relativo contra `.cwd`, aplica `git_info` al ancestro
  existente y devuelve `top`, `common` y `file_canon`. Fuera de un repo devuelve vacío.
- `map_to_main <path>`: canoniza el path. Si cae en un worktree enlazado W con un principal M válido, devuelve
  `canon(M) + rel(path, W_top)`; si no, el path canónico. Validado con prototipo: raíz de W, W/apps/web,
  hermano, symlink y `/tmp`.
- `state_root <cwd>`: `map_to_main` aplicado a la base, que es `$CLAUDE_PROJECT_DIR` → git toplevel del cwd →
  cwd.

  Casos: si la base es la raíz de W, el resultado es M; si es un subproyecto, `M/apps/web`; un worktree
  hermano funciona; un submódulo dentro de W se queda donde está (documentado).
- `scope_locate <cwd> <sid>` (para scripts sin `CLAUDE_PROJECT_DIR`), en orden:
  1. Si hay `CLAUDE_PROJECT_DIR`, `scope_file_for_session(state_root)`.
  2. Subir desde `map_to_main(cwd)` buscando `.claude/sessions/<sid>-task-scope.json`.
  3. `M/.claude/sessions` directo.
  4. `find M -maxdepth 5` (el subproyecto queda a profundidad 5), podando `node_modules`, `.git` y
     `.claude/worktrees`. Si hay varios resultados, gana el más reciente según `ls -t` (portable entre macOS y
     Linux).
  5. El legacy `.claude/task-scope.json`. Si el sid queda vacío tras sanitizar, se salta directo aquí.

  Es de solo lectura, sin punteros.
- `project_root`: **conserva su salida actual exacta** (sin canonizar). Solo cuando `is_linked_worktree` es
  verdadero **sobre la base** (`CLAUDE_PROJECT_DIR` o el toplevel, no `.cwd`) devuelve `map_to_main`. Así, una lib nueva con un `scope-gate` viejo (consumidor con skip-local,
  `install-hooks.sh:115`) no falla abierto por el symlink. Sus 3 llamadores (`scope-gate:34`,
  `task-scope-updater:23`, `intent-bootstrap:24`) y `task-verify` comparten el scope. Los hooks nuevos usan
  `state_root`, que sí canoniza.
- Patrón de uso en los hooks:
  - `ROOT=""`;
  - `[ -f "$LIB" ] && . "$LIB" && declare -F state_root >/dev/null && ROOT=$(state_root "$CWD")`;
  - fallback: git toplevel de `$CWD` y luego `$CWD`.

## Fase 2: hooks y scripts de estado (H3, H4, H8)
**Orden de edición** (los hooks aplican en cuanto se guardan):
1. La lib, y luego `scope-gate.sh`.
   - `scope-gate` canoniza **localmente** ROOT y FILE (`canon_path` bajo `declare -F`, con fallback al valor
     crudo).
   - ROOT se canoniza **inmediatamente después de `project_root` (línea 34) y antes de
     `scope_file_for_session`**, para que `SCOPE_FILE` herede el prefijo canónico. Si no, `SCOPE_RELATIVE`
     quedaría absoluto y se bloquearía la edición del propio scope.
   - Como `project_root` no cambia fuera de los worktrees, el orden ya no es crítico.
   - La jurisdicción y los worktrees siguen en la Fase 3.
2. **`skill-load-gate.sh`, lo editas tú**, porque `protect-skill-gate-bypass.sh:36` lo bloquea siempre. Te
   paso el diff: lee el marcador en `$ROOT/.claude/sessions` **o** en `./.claude/sessions`.
3. `track-skill-load.sh` (PostToolUse `*`):
   - Escribe en `$ROOT`, y durante una release también en `./.claude/sessions` si `./.claude/.gitignore` lista
     `sessions/`.
   - **Además**, cuando el input no trae `agent_id`, registra `$ROOT/.claude/sessions/<sid>-main-top` con el
     toplevel canónico de `.cwd`. Esto captura `EnterWorktree`, `ExitWorktree` y los `cd` dentro del mismo
     turno.
   - **Guard de costo sin forks:**
     - Un solo `jq -r '@sh'` extrae sid, `agent_id`, cwd y el match.
     - Se cachea el último `.cwd` en `${TMPDIR:-/tmp}/claude-$UID/` (modo 700, con verificación de dueño y de
       que no sea symlink) como `tsl-<sid sanitizado>.cwd`, y se lee con `read`.
     - Se salta solo si `cache == cwd` **y** el registro existe.
     - Si no, se calcula ROOT y el toplevel (unos 10-25 ms), se escribe el registro y **después** el cache
       (mktemp más `mv -f`).
     - Los eventos con `agent_id` no leen ni escriben ni el cache ni el registro.
   - Ambos lados de la comparación `toplevel == main-top` pasan por `canon_path`.
   - Hueco documentado: `cd W && task-verify` en un solo comando lee un registro viejo y no borra el scope,
     que es la dirección segura.
4. `skill-autoload` y `plan-mode-gate`: marcadores, escape hatches, la escritura en `.claude/.gitignore` y la
   lectura de `SKILL_FILE`/`AGENTS_FILE` pasan a `$ROOT`.
5. El par `quality-enforce` + `ts-quality-gate`, juntos:
   - Estado en `$ROOT/.claude/quality-state`. La clave es `file_canon` relativo a ROOT, o absoluto si cae
     fuera; la regla es idéntica en los dos hooks.
   - `ts-quality-gate`:
     - Busca el `tsconfig.json` más cercano, con tope en el `top` de `repo_of_file`.
     - Usa el tsc local (`node_modules/.bin`) o el del PATH, nunca `npx`. Sin tsc, sale sin tocar el estado;
       el mensaje de bloqueo de `quality-enforce` menciona "sin tsc → borrar el estado a mano".
     - Usa el exit code de tsc, grep con `-F` anclado a `^<rel>(`, y corrige el bug de `grep -c || echo 0`.
   - Cambio documentado: el gate se enciende en monorepos sin un tsconfig en la raíz, y los tsconfig de
     solución no verifican nada.
6. `edit-drift-detector`: estado bajo ROOT; contador `edit-count-<sid sanitizado>`, con fallback a
   `cksum(ROOT)`; corregir el mensaje de la línea 32.
7. `scripts/task-verify.sh`:
   - El scope sale de `scope_locate`, llamado con el cwd **anterior** al `cd` de la línea 19, con el guard
     `declare -F`.
   - El checklist corre en el toplevel actual.
   - **Regla de borrado del scope:** si la Fase 0 (g) da un discriminador estable, se borra solo si **no** es
     un subagente. Si no lo da:
     - `task-verify` lee `<sid>-main-top` junto al scope encontrado (`dirname "$SCOPE_FILE"`), que escribe
       `track-skill-load` (paso 3).
     - Borra solo si el toplevel actual canónico coincide con ese registro.
     - Sin registro, borra si no es un worktree enlazado.
   - Documentado: un subagente **no aislado** comparte sid y podría borrar el scope del padre; eso ya pasa
     hoy.

## Fase 3: hooks sobre el repo del archivo y guards de escritura (H5, H6, H7)
- `scope-gate.sh`:
  - `RELATIVE` se calcula desde `file_canon` contra el ROOT canónico.
  - Si no matchea, o si cae en `.claude/worktrees/`, y el `common` coincide con el de ROOT, se recalcula
    contra el `top` del worktree.
  - Jurisdicción = mismo common-dir (se reescribe el comentario de las líneas 54-56); expansiones citadas.
- `adr-gate.sh`:
  - `REPO_ROOT` y FILE salen de `repo_of_file`; fuera de un repo, exit 0.
  - El regex de "ADR nuevo" pasa a `^(\?\?|A.)`, para aceptar `AM`.
  - `CONFIG_FILE="$REPO_ROOT/.claude/adr-gate.conf"` (líneas 34, 35 y 101).
  - El ADR documenta que la numeración es por worktree.
- `protect-secrets.sh`: `REPO_ROOT` sale de `repo_of_file`; fuera de un repo sigue escaneando.
- `.agents/sync_skills.sh` (no está bajo el gate: el prefijo protegido es `.agents/skills/`):
  - `REPO_ROOT` desde `BASH_SOURCE`, con fallback a git.
  - Si `git-dir` ≠ `common-dir` (inline, sin lib), exit 0.
  - El ADR registra que en una sesión de subproyecto la raíz pasa a ser la del subproyecto.
- `auto-sync-daily.sh`:
  - Invoca `(cd "$REPO_ROOT" && bash .agents/sync_skills.sh)`, que es la vía por la que el fix llega a los
    consumidores.
  - Si `REPO_ROOT` es un worktree enlazado, exit 0.
- `.claude/settings.json:285`: `cd "$CLAUDE_PROJECT_DIR" && bash .agents/sync_skills.sh 2>/dev/null || true`.
- `block-dangerous.sh` regla 5: canonizar el target y cada entrada de `worktree list`.
- `scripts/_cc-lib.sh:4,7`: `-e "$dir/.git"`.

## Fase 4: worktree-isolation-gate (H9)
- Se elimina el Case 2 (Agent) y el matcher queda en `Workflow`.
- Case 1:
  - Emite solo `{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":…}}`, **sin**
    `permissionDecision` (ni `allow` ni `defer`).
  - Avisa si `#agent(` es mayor que `#isolation:\s*['"]worktree`, cubriendo `Promise.all`.
  - Sin `HAS_WRITES`.
- Actualizar la cabecera y `settings.json:153`.

## Fase 5: release.sh, install_hooks, doctor
- `scripts/release.sh`:
  - `.git/shallow` (línea 151) y `.git/CHERRY_PICK_HEAD` (línea 567) pasan a
    `git rev-parse --path-format=absolute --git-path`.
  - El `wt_path` por defecto se calcula relativo a la primera entrada porcelain.
  - `worktree clean`: `--porcelain`, "commit propio" por el reflog de la rama, y sin reflog no se lista.
- `tools/install_hooks.sh`:
  - `--path-format=absolute --git-path hooks`.
  - El gate completo pasa a `commit-msg`, con `--commit-message "$(grep -v '^#' "$1")"`.
  - Migra el bloque viejo de `pre-commit`.
  - Usa el BANNER de línea completa para distinguir el archivo propio de uno ajeno.
  - `[ -f tools/check_adr_gate.py ] || exit 0`.
  - Si `core.hooksPath` es global o husky, avisa y no instala.
  - Instalarlo en este repo. El ADR registra que `gitops-init.sh:245` no actualiza a los consumidores.
- `scripts/harness-doctor.sh`, sección de worktrees (solo lectura, exit 0, `git -C` y guard de git):
  - huérfanos;
  - conteo sin el principal (más de 4 → aviso);
  - mergeados con al menos 1 commit propio, sin fetch;
  - `.ai-tooling` sin `.worktreeinclude` → aviso.
- `tests/harness/audit-consumer.sh:77`: `CLAUDE_PROJECT_DIR="$CONSUMER"`.

## Fase 6: configuración nativa
- `.claude/.gitignore`: `/worktrees/`.
- `.worktreeinclude`: `.env`, `profile-envs/`, `cloud-provider-env/`, `.cloudsql-env`,
  `.claude/settings.local.json` (best effort).
- `intent-bootstrap.sh` (líneas ~41 y ~102, desplazadas por cambios sin commitear): `PRUNE` excluye
  `./.claude/worktrees`.

## Fase 7: completar el ADR y la documentación
- ADR-0073 completo:
  - formato ES, `check_adr_sections.py --i18n-es`;
  - hallazgos, resultados de la Fase 0, decisión de no usar Worktree* y enmienda a ADR-0044;
  - límites conocidos: numeración por worktree, squash-merge, `settings.local.json` best effort, el proxy solo
    desde el principal, un submódulo dentro de un worktree no se mapea, y un subagente no aislado puede borrar
    el scope.
- `docs/adr/index.md`: fila 0073, y 0072 → Aceptado. Nota en ADR-0044.
- `gitops-monorepo/SKILL.md`, subsección "Worktrees nativos":
  - `EnterWorktree`;
  - `ExitWorktree`: commit primero y luego `remove`, porque `discard_changes` lo bloquea `block-dangerous`
    por diseño;
  - `.worktreeinclude`, `worktree.sparsePaths` (con `.claude`), `symlinkDirectories`, `baseRef`;
  - el subagente con `isolation`;
  - no levantar el proxy desde un worktree;
  - cuándo usar el worktree nativo y cuándo `release.sh`.
- `frontend-analysis/SKILL.md:3,18`: quitar la afirmación falsa.
- `AGENTS.md:65` y `workflow-coordinator.md:42`: ADR-0044/0073 más las keywords.
- `CLAUDE.md`: tabla de hooks, y "Instalación en un nuevo proyecto" con `.worktreeinclude` y `/worktrees/`.
- `ai-notes/AI_LEARNING.md`.

## Fuera de alcance (registrado en el ADR)
- `install-hooks.sh:251` borra los hooks propios del consumidor.
- Falso positivo de `protect-skill-gate-bypass` con `>`.
- La deriva de CLAUDE.md y del índice de ADR.
- Docker/proxy por worktree (`container_name` y puerto fijos, `.venv-*` sin ignorar).
- El slug de `session-audit.sh`.
- Los symlinks de `init.sh`/`ralph-install.sh`.
- El `cd` de `migration-gate`.

## Verificación
1. Fase 0 registrada en el ADR. Recontar las aserciones actuales de `run-tests.sh`.
2. Tests nuevos, con fixtures git reales: `git init`, `-c user.*`, `-c commit.gpgsign=false`,
   `-c core.hooksPath=/dev/null`, `unset GIT_DIR GIT_INDEX_FILE`, `CLAUDE_PROJECT_DIR` explícito y un symlink
   de doble montaje.
   - **lib:**
     - `is_linked_worktree`: principal → no; worktree → sí; **submódulo → no**; **principal con
       `--separate-git-dir` → no**.
     - `state_root`: desde un subdirectorio, la raíz del worktree o un hermano → M; `CLAUDE_PROJECT_DIR=
       apps/web` → `apps/web`; el subproyecto dentro de un worktree → `M/apps/web`; layout bare → **W, sin
       mapear**; `cd` a un submódulo → no se mueve; base vía symlink → mapea bien; con un `ROOT` heredado →
       lo ignora.
     - `repo_of_file`: `/tmp`, FILE relativo, espacios.
     - `scope_locate`: sin `CLAUDE_PROJECT_DIR` en un subproyecto (profundidad 5) y desde un worktree → lo
       encuentra; con un scope basura más profundo → gana el más reciente.
     - `git_info` simulando git viejo (eco de `--path-format`) → el principal no se clasifica como enlazado.
   - **adr-gate:** el ADR en estado `AM` → permitido.
   - **compatibilidad:**
     - lib nueva + `scope-gate` viejo, con ROOT y `file_path` vía el **mismo** prefijo symlink → sigue
       bloqueando;
     - `project_root` fuera de un worktree → idéntico al actual;
     - `CLAUDE_PROJECT_DIR` vía symlink, en validate, editando el propio scope → exit 0.
   - **track-skill-load:** el mismo `.cwd` dos veces → sin fork de git (contar invocaciones con un `git` shim
     en el PATH).
   - **marcadores:** todo cae en `$ROOT`, sin basura. Lib ausente → no escribe en `/.claude`.
     `skill-load-gate` encuentra el marcador en ROOT o en `./`.
   - **quality:** misma clave desde un subdirectorio. Dos worktrees no colisionan. Sin tsc → **el estado
     queda intacto**. `x.ts` no matchea `x.tsx`.
   - **scope-gate:**
     - worktree anidado en analysis, path permitido → 0;
     - hermano en validate → 2;
     - clon ajeno → fuera de jurisdicción;
     - ROOT con symlink → no falla abierto.
   - **adr-gate:** skill en un worktree sin ADR → 2; config desde el repo del archivo. **protect-secrets:**
     secreto en un worktree anidado → detectado.
   - **guards:** `sync_skills` y `auto-sync-daily` en un worktree enlazado → exit 0 sin escribir; en un
     subdirectorio o un submódulo → sí sincroniza.
   - **task-verify:**
     - simulando un subagente desde un worktree → no borra;
     - el hilo principal tras `EnterWorktree` → **sí** borra.
   - **block-dangerous:** `rm -rf` de un worktree con el prefijo alterno → bloqueado.
   - **isolation-gate:** 2 `agent(` y 1 isolation → `additionalContext`, **sin** `permissionDecision`.
   - **install_hooks:** desde un worktree → `commit-msg` en `--git-path hooks`; `[skip-adr]` del mensaje
     actual; un pre-commit ajeno sobrevive dos corridas.
   - **release.sh:** cherry-pick con conflicto en un worktree → detectado.
   - **doctor:** un worktree nativo nuevo no aparece como mergeado.
3. `bash tests/harness/run-tests.sh` en verde.
4. E2E real: `EnterWorktree` → editar → marcadores únicos, sin bloqueos del skill gate, skills intactos,
   `.env` presente → commit → `ExitWorktree remove`.
5. `harness-doctor.sh` sale con exit 0; `check_adr_sections.py --i18n-es` pasa; `./scripts/task-verify.sh`
   sale con exit 0.
