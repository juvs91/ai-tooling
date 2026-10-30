# Plan: cerrar los pendientes de ADR-0073 en el harness (ADR-0074), versión 5 (convergida: la ronda 5 no pidió cambios materiales)

Estado: ejecutado (2026-10-29) — ver ADR-0074.

## Contexto
ADR-0073 (commit 750b7a3) dejó cuatro pendientes. La versión 5 incorpora 5 rondas de validación adversarial
y tres decisiones del usuario.
1. **`install-hooks.sh` borra los hooks propios del consumidor.** `jq '.hooks = $hooks'` (línea ~251) reemplaza
   todo `.hooks` de `settings.local.json`.
2. **Falso positivo de `protect-skill-gate-bypass.sh`.** El ramal Bash bloquea cualquier comando que mencione
   `skill-load-gate.sh` o `no-skill-gate` y contenga un `>` en cualquier parte (por ejemplo `2>/dev/null`).
   También tiene huecos: `rm`, `python -c open(...)`, `find -delete`, `dd of=`, `git checkout --`.
3. **Configuración de worktrees en consumidores** (decisión del usuario): la provee `install-hooks`. Solo
   crea lo que falta y nunca sobrescribe.
4. **Gate de ADR en git** (decisión del usuario): **solo migrar** los clones que ya tenían el gate en la
   instalación vieja (pre-commit). Si falta, solo se avisa. Los clones donde el framework pre-commit **gestiona el gate** (no solo el lint) o con
   `core.hooksPath` cuentan como "no aplica".

**Hechos de la validación:**
- Los consumidores de `gitops-init` usan el **framework pre-commit**: `.pre-commit-config.yaml` con
  `id: adr-gate` y `pre-commit install --hook-type commit-msg`. Para ellos el instalador propio no aplica,
  porque agregaría un bloque después del `exec` del framework que nunca se ejecutaría.
- Ningún hook del toolbox se ha borrado ni renombrado nunca, así que la primera migración no deja entradas
  huérfanas.
- La CLI de `check_adr_gate.py` (`--changed-files/--new-files/--commit-message`) es estable en todo el
  historial.
- `grep -qxF` de `OLD_BANNER` y `BLOCK_BEGIN` matchea todas las versiones históricas del instalador.
- En ai-tooling nada corre el doctor automáticamente.

## Cambios

### 1. `scripts/install-hooks.sh`: merge de `.hooks`, calculado antes de escribir
- **Propiedad.** `owned` = distribuibles `.sh` con `# event:` y `# timeout:` (los que el harness puede
  registrar; **no** `task-scope-lib.sh`) ∪ `manifest.registered[]` ∪ basenames de
  `manifest.files[".claude/hooks/*"]`. Los nombres deduplicados (ya presentes en `settings.json`) siguen en
  `owned`.
- **Match por segmento de ruta**, no por prefijo:
  `test("(^|[\\s/\"'])\\.claude/hooks/" + (basename con `.` escapado) + "([\\s\"';&|)]|$)")`. Validado en jq
  1.7.1 con 8 formas de comando. Cubre la forma
  relativa `bash .claude/hooks/x.sh` (evita ejecutarlo dos veces) y no matchea `x.sh.bak`.
- **Filtro hook por hook, no por grupo:**
  `.hooks[ev] |= (map(.hooks |= map(select(owned(.command)|not))) | map(select(.hooks|length>0)))`. Después
  se agregan los grupos del harness y se eliminan los eventos vacíos. Un hook del consumidor que comparte
  grupo con uno nuestro se conserva.
- **Orden y seguridad:**
  - El JSON resultante se calcula en la fase de **plan**. Si `settings.local.json` no es JSON válido, se
    aborta **antes** de copiar nada.
  - Se escribe solo si el resultado cambió (`jq -S` + `cmp`).
  - `NEEDS_BACKUP=true` si cambian settings o `.gitignore`.
  - Se persiste `manifest.registered` solo con `--apply`, antes de copiar el manifest. Ahí van únicamente
    los nombres escritos en local, sin los deduplicados.
  - El dry-run reporta "preservados N / quitados X".
  - `trap` limpia el manifest temporal.
- **Configuración de worktrees** (sección nueva del plan; se salta si el destino es un worktree enlazado,
  git-dir ≠ common-dir):
  - **`.worktreeinclude`:** si falta y no hay entrada en el manifest, se crea desde
    `templates/worktreeinclude.template` (nuevo: `.env`, `.env.local`, `.claude/settings.local.json`) y se
    hace `m_set ".worktreeinclude" <sha> harness`.
  - Si falta pero el manifest **sí** tiene la entrada, sale "SALTAR — eliminado por el usuario (declinado)".
    Solo `--force` lo recrea. Si existe, no se toca.
  - **`/worktrees/` en `.claude/.gitignore`:** la detección es una línea exacta,
    `grep -qxE '[[:space:]]*/?worktrees/?[[:space:]]*'`. Antes de agregar se garantiza el salto de línea
    final, y se agrega con `>>` (O_APPEND, compatible con el append concurrente de `sessions/`). Se marca
    `manifest.provisioned.gitignore_worktrees=true`; si el usuario quita la línea después, se respeta.
  - **Rollback:** el backup incluye `.gitignore.bak` y `CREATED.txt` (archivos creados →
    "rollback = copiar los .bak y borrar lo listado en CREATED").

### 2. `.claude/hooks/protect-skill-gate-bypass.sh`: misma regla amplia, exenciones probadas
Se descarta el parser "solo destino por segmento": perdía casos que hoy sí se bloquean (`xargs touch`,
`F=…; touch $F`, `mv gate /tmp`, `>|`, `find -exec`). Se mantiene la regla del comando completo (menciona el
archivo **y** hay una señal de escritura) y solo se quitan los constructos cuyo destino es **probadamente
seguro**:
1. **Prefiltro:** si no menciona `skill-load-gate\.sh` ni `no-skill-gate`, exit 0.
2. **Redirecciones** en todo el string, con el regex
   `[0-9]*(>>|>\||&>>|&>|>&|<>|>)[[:space:]]*(("[^"]*"|'[^']*'|[^[:space:];&|()<>"'])+)`: alternativas de la más larga a la más corta y
   clases POSIX, porque el `sed -E` de macOS no soporta `\s`. Se sacan las comillas del destino:
   - el destino se captura respetando comillas (`"a b"`, `'x'`, `$'…'`) y se corta en `;`, `&`, `|`, `(` y
     `)`. Así no se traga el verbo siguiente (`true >/tmp/x;touch <marker>`) ni se corta a mitad de un
     destino entre comillas (`> "/tmp/q b/../<gate>"`). Si una comilla queda sin cerrar, el `>` no se borra
     y bloquea como redirección no resuelta (fail-closed);
   - destino con nombre protegido → **bloquear**;
   - fd dup (dígito o `-`) o `/dev/(null|stderr|stdout|tty)` → seguro;
   - contiene `$`, `` ` ``, `*`, `?`, `[` o `{` → señal de escritura (no resuelto);
   - otra ruta literal → seguro.

   Luego se borran todas las redirecciones del string.
3. **`tee`:** si un argumento menciona un nombre protegido → bloquear; si no, se quita la cláusula.
4. **`cp`:** se permite solo si el nombre aparece únicamente en argumentos no finales, no hay `-t` ni
   `--target-directory`, y el último argumento es literal, no termina en `/` y no es un directorio (relativo
   a `.cwd`). Si no, bloquear.
5. **Fallback:** si lo que queda menciona un nombre protegido **y** contiene un verbo de escritura **en
   posición de comando**, bloquear.
   - Posición de comando: inicio de **línea** o del comando, o después de `;`, `&`, `|`, `(`, `$(`, un
     backtick, `{`, `!`, `then`, `do`, `else`, `elif`, `if`, `while`, `until`, `xargs` o `-exec`.
     Opcionalmente precedido de `sudo`, `env`, `VAR=`, `command`, `exec`, `time`, `nohup` o `timeout N`. El
     verbo también cuenta con ruta absoluta (`/usr/bin/touch`) o escapado (`\rm`). El chequeo es línea por
     línea, así que el newline cuenta como separador. Así `grep -ln`, `docker run --rm`,
     `git format-patch` y `grep … install-hooks.sh` no son falsos positivos.
   - Para `python3?`, `node`, `ruby` y `perl`, solo cuentan las formas `-c`, `-e` o stdin.
   - Verbos:
   - `touch`, `tee`, `mv`, `install`, `ln`, `rm`, `unlink`, `truncate`, `chmod`, `chown`, `dd`, `rsync`;
   - `sed` con `-…i` o `--in-place`, `perl -…i`, `python3?`, `node`, `ruby`, `perl -e`, `(ba|z)?sh -c`,
     `eval`, `xargs`;
   - `find … -delete|-exec`, `git (checkout|restore|apply|stash|mv|rm)`, `patch`;
   - una redirección no resuelta.

Huecos aceptados y documentados: globs o variables que nunca escriben el nombre, y la edición de
`settings.json`. El threat model sigue siendo un "badén", porque el gate igual acepta el marcador
`*-skill-loaded`. Los ramales Write/Edit no cambian.

### 3. `tools/install_hooks.sh --check`: fuente única de verdad
Modo nuevo, solo lectura. La cabecera del archivo declara `# supports: --check` (ver §5).

Códigos de salida (rango 10-13, para no chocar con el 1 de `set -e` ni con el 2 de bash/argparse):
- **10** = al día:
  - el commit-msg es nuestro, ejecutable, y su contenido completo es idéntico al esperado (comparado con
    `cmp`; el contenido es estático);
  - o, en modo bloque, el texto entre BEGIN y END (extraído con awk) es igual a `GATE_BODY`.
- **11** = requiere migración: rastros en pre-commit (`OLD_BANNER` o `BLOCK_BEGIN`, incluido un bloque viejo
  agregado a un pre-commit del framework) o commit-msg propio desactualizado.
- **12** = falta: no hay rastro del gate. Incluye el caso de `.pre-commit-config.yaml` con `id: adr-gate`
  pero **sin** el hook del framework instalado; el mensaje dice "corre `pre-commit install --hook-type
  commit-msg`".
- **13** = no aplica:
  - el directorio no es un repo git;
  - `core.hooksPath` definido (cambio de comportamiento que va al ADR: ya no se actualizan instalaciones en
    `.githooks/`);
  - no existe `tools/check_adr_gate.py`;
  - el framework gestiona el gate:
    - `commit-msg` generado por el framework (`grep -qF 'File generated by pre-commit'`, porque la línea
      incluye una URL), o
    - `pre-commit` generado por el framework **y** `.pre-commit-config.yaml` declara `id: adr-gate`.
      La detección ignora líneas comentadas, con el regex
      `^[[:space:]]*(-[[:space:]]+)?id:[[:space:]]*["']?adr-gate["']?[[:space:]]*(#.*)?$`. El flow style no se
      detecta y cae a la clasificación de commit-msg (falla a lo seguro).

    En ambos casos, sin rastros nuestros en pre-commit. Un repo que usa el framework **solo para lint** (solo
    `pre-commit`, sin `id: adr-gate`) **no** es 13: sigue a la clasificación de commit-msg. Ese es el caso
    que contempla `templates/gitops/.pre-commit-config.yaml.template:47-48`.

Cualquier otra salida (14 vía `set -E` + `trap ERR`, o 1 por `set -u`, entre otras) significa **error**, y los
llamadores solo la registran en el log.
- `--check` se parsea antes del exit de "no es git" (que pasa a 13).
- La clasificación devuelve su código **por stdout** (`CODE=$(classify)`), nunca como el retorno de una
  llamada desnuda, porque con `-E` el trap lo convertiría en 14.
- **El modo instalación** sale con 0 en éxito y en "no aplica"; nunca con códigos 10-13.

**Clasificación única** (la comparten `--check` y la instalación), en este orden:
1. no aplica de entorno (no es git, `hooksPath`, sin checker) → 13;
2. rastros en pre-commit → 11;
3. framework instalado → 13;
4. commit-msg al día → 10, desactualizado → 11, ausente → 12.

**Instalación:** siempre ejecuta la limpieza de rastros en pre-commit, aunque el resultado sea "framework →
13". Solo se salta la escritura de commit-msg en el caso 13. Así una migración no se repite todos los días.

Gap aceptado: el framework mueve un hook previo a `<hook>.legacy` y lo sigue corriendo, así que un gate viejo
puede sobrevivir en `pre-commit.legacy`. Se documenta.

**Preflight:** `grep -q -- --commit-message <<<"$(python3 tools/check_adr_gate.py --help 2>&1)"` (sin pipe,
para evitar SIGPIPE con pipefail); si falla, no se instala y se avisa.

**Limpieza adicional:** un bloque `BLOCK_BEGIN` muerto que el instalador actual dejó después del `exec` en un
`commit-msg` del framework se quita en una instalación manual. Como `--check` da 13, auto-sync no lo toca; no
pasa nada, porque es código muerto.
El preflight usa `"$(python3 … --help 2>&1 || true)"` para que el trap no imprima ruido.
Todo `grep -q` o `cmp -s` dentro de la clasificación va en un `if` o con `||`, para que no dispare el trap.

**Backup:** antes de borrar o reescribir `pre-commit`/`commit-msg`, se copian a un `.bak` junto al hook.

### 4. `.claude/hooks/auto-sync-daily.sh`: subsistema "git-hooks", solo migración
- Corre **antes** del doctor.
- Condiciones: `[ -n "$AI_TOOLING_DIR" ]`, flag `sync_git_hooks` (default true) y existe
  `tools/check_adr_gate.py`.
- Ejecuta `(cd "$REPO_ROOT" && bash "$AI_TOOLING_DIR/tools/install_hooks.sh" --check)`. Siempre el
  instalador del **toolbox**, nunca la copia del consumidor:
  - **11** → copia los hooks git actuales a `.claude/.harness-backup-<ts>/`, corre el instalador y lo
    registra en el log;
  - **12** → solo log con el comando a correr;
  - **10 y 13** → nada;
  - otro código → log de error.
- Idempotente: una segunda corrida devuelve 10 o 13, sin cambios.

### 5. Avisos: doctor y `task-verify` usan el mismo `--check`
- **Qué instalador se usa para el check**, en este orden:
  1. el del toolbox (`$AI_TOOLING_HOME` o `local_path` del `.ai-tooling`, misma resolución que el doctor en
     sus líneas 39-50);
  2. si no hay toolbox, la copia del repo.

  **Todo** candidato, incluido el del toolbox, se usa solo si existe (`-f`) **y** declara la línea exacta
  `# supports: --check` (`grep -qxF`). Una versión vieja ignoraría `--check` y escribiría hooks; esto también
  cubre un toolbox que regresó a una versión anterior;
  3. si ninguna sirve, se omite el chequeo sin imprimir nada.

  Siempre se ejecuta como `OUT=$(cd "$REPO_ROOT" && bash … --check 2>&1); rc=$?`. **No** se usa el patrón
  `if OUT=$(…)` del doctor y auto-sync, que trataría el 10 como fallo.
- **task-verify** resuelve el toolbox **inline**, porque no puede fuentear el doctor:
  - lee el marcador `.ai-tooling` en `$PWD` (ya está en el toplevel después de la línea 20);
  - toma `AI_TOOLING_HOME`, o si no `local_path` con expansión de `~` y de `$HOME` (igual que auto-sync,
    línea 87);
  - sin marcador, pasa a la copia del repo sin salir (no hace el `exit 0` del doctor);
  - verifica con `[ -d ]` que la ruta del toolbox exista;
  - el aviso va antes del `exit 0` del ramal de éxito.
- `scripts/harness-doctor.sh`:
  - Sección "ADR gate git": 11 o 12 → WARN con el comando; 13 → "no aplica"; 10 → nada; otro → `warn`
    "check con error (rc=N)". Va por `warn()`, no `say()`, para que llegue al `sync.log`.
  - Además, el aviso "sin `.worktreeinclude`" se omite si el manifest lo marca como declinado.
- `scripts/task-verify.sh`: al final (exit 0) muestra ese mismo aviso si `--check` ∈ {11,12}. Es
  informativo y no cambia el exit code. Nota: `task-verify` sale antes si no hay scope, así que el aviso solo
  aparece cuando existe un scope.
- `init.sh`: agrega el paso **después** de los chequeos de `python3` y `git` (línea ~84):
  `(cd "$PROJECT_DIR" && bash tools/install_hooks.sh) || log_warn "…"`.

### 6. ADR y documentación
- `docs/adr/ADR-0074-harness-merge-y-hooks-git.md`; en ADR-0073 "Fuera de alcance", marcar lo resuelto;
  fila en `index.md`.
- `CLAUDE.md`: "Instalación en un nuevo proyecto" (el paso 4 ahora lo hace install-hooks) y la fila de
  install-hooks.
- `ai-notes/plans/2026-10-29-pendientes-adr0073.md` y `AI_LEARNING.md` (incluye la regla: al listar el
  entorno, filtrar por nombre exacto).
- **Commit:** el mensaje se escribe con Write a un archivo y se usa `git commit -F`, porque el mensaje
  menciona los archivos protegidos y el hook podría bloquearlo. Los tests arman esos nombres en runtime
  (`"no-skill""-gate"`).

## Archivos
`scripts/install-hooks.sh`, `templates/worktreeinclude.template` (nuevo),
`.claude/hooks/protect-skill-gate-bypass.sh`, `tools/install_hooks.sh`, `.claude/hooks/auto-sync-daily.sh`,
`scripts/harness-doctor.sh`, `scripts/task-verify.sh`, `init.sh`, `tests/harness/run-tests.sh`,
`tests/harness/bypass-gate-tests.sh` (nuevo), `tests/harness/githook-tests.sh` (nuevo), `docs/adr/…`,
`CLAUDE.md`, `ai-notes/…`.

## Verificación
1. **install-hooks** (en `run-tests.sh`):
   - un grupo mixto (hook del harness + `my-hook.sh` en el mismo matcher) conserva `my-hook.sh`;
   - un comando relativo `bash .claude/hooks/block-dangerous.sh` queda registrado una sola vez;
   - dos `--apply` no duplican nada;
   - sin cambios → `settings.local.json` intacto (mismo mtime) y sin directorio de backup;
   - `settings.local.json` inválido → aborta con 0 copias;
   - el dry-run no escribe y reporta "preservados";
   - `.worktreeinclude`: se crea si falta, uno propio no se toca, y si se borra después de crearlo no se
     recrea (declinado), salvo con `--force`;
   - `.gitignore` sin salto de línea final → la línea `/worktrees/` queda limpia, y no se duplica en dos
     corridas.
2. **bypass-gate** (archivo nuevo, con la lista completa de la validación):
   - **permitidos (0):** `cat … 2>/dev/null`, `ls … 2>&1`, `sed -n … > /tmp/x`, `cp gate /tmp/x`,
     `grep … | tee /tmp/y`, `git log/diff/add -- gate`, `diff … >/dev/null`;
   - **bloqueados (2):** todas las formas de redirección hacia el archivo (`>`, `>>`, `>|`, `>&`, `&>`,
     `2>`, `exec 3>`/`3<>`, heredoc, destino entre comillas), `bash -c`, `tee -a`, `cp` hacia el archivo o
     hacia `hooks/`, `mv` con el archivo como origen o destino, `rm`, `unlink`, `truncate`, `chmod`,
     `touch`, `ln`, `install`, `dd of=`, `sed -i`/`-Ei`, `perl -pi`, `python3 -c open`, `find -delete`,
     `git checkout --`, `xargs touch`, `F=…; touch $F`, `F=…; echo > $F`, comandos separados por newline y
     `$(touch …)`;
   - Write/Edit → 2.
3. **githook** (archivo nuevo, fixtures git):
   - `--check` da 12 sin hook, 11 con `OLD_BANNER` en pre-commit, 11 con un bloque viejo, 10 después de
     instalar, y 11 si el commit-msg propio fue alterado;
   - da 13 con el framework instalado, con `core.hooksPath`, sin el checker y fuera de un repo git;
   - da 12 con `.pre-commit-config.yaml` (`id: adr-gate`) pero sin el framework instalado;
   - con el framework solo en `pre-commit` (lint, sin `id: adr-gate`) más nuestro `commit-msg`, da 10, 11 o
     12, nunca 13;
   - el modo instalación sale con 0 en éxito y en "no aplica";
   - un instalador del toolbox sin `# supports: --check` no se usa: auto-sync no escribe hooks;
   - un pre-commit del framework con un bloque viejo se limpia una vez, y el siguiente `--check` da 13;
   - auto-sync:
     - el caso 11 migra (pre-commit ajeno preservado y backup creado);
     - el caso 12 solo deja log, sin escribir hooks;
     - `sync_git_hooks:false` no hace nada;
     - una segunda corrida no cambia nada;
   - un checker sin `--commit-message` hace que el preflight no instale;
   - una copia vieja del instalador (sin `# supports: --check`) **no** se usa para el check: doctor y
     task-verify no escriben hooks.
4. **doctor:** WARN con 11 o 12; "no aplica" con 13; nada con 10; "error" con otro código.
   **task-verify:** aviso solo con 11 o 12 (y solo si hay scope).
5. **bypass-gate extra:**
   - permitidos (0): `grep -ln gate`, `docker run --rm … gate`, `git format-patch`,
     `grep gate scripts/install-hooks.sh`;
   - bloqueados (2): `x >| <marker>`, `true >/tmp/x;touch <marker>`, `cat a 2>/dev/null&&rm <gate>`,
     `echo hi>/tmp/q|tee <marker>`, `echo x > "/tmp/q b/../<gate>"`, `if true; then touch <marker>; fi`,
     `{ touch <marker>; }`, `nohup touch <marker>`, `\rm <gate>`, `/usr/bin/touch <marker>`;
   - comentado `# - id: adr-gate` → no cuenta como gate declarado.
6. `bash tests/harness/run-tests.sh` en verde (17 + 38 + los nuevos).
7. `./scripts/task-verify.sh` sale con exit 0 → `git commit -F` (el gate `commit-msg` lo valida) → push a
   master.

## Límites documentados (van al ADR)
- Un `id: adr-gate` editado a mano con `stages: [commit-msg]`, con el framework instalado solo en
  pre-commit, da 13 aunque el gate nunca corra.
- Con la config declarada pero sin el framework, una instalación manual escribe nuestro commit-msg. Si
  después se corre `pre-commit install`, el framework lo mueve a `commit-msg.legacy` y el gate corre dos
  veces (lo advierte la nota de la plantilla). El mensaje del código 12 lo menciona.
- Un gate viejo puede sobrevivir en `pre-commit.legacy` (el framework lo sigue corriendo).
- Con `core.hooksPath`, ya no se actualizan instalaciones en `.githooks/`.
- Ocultar el nombre con globs o variables que nunca lo escriben, y editar `settings.json`.
