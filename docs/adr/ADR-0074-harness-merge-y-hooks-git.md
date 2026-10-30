# ADR-0074: Merge de hooks del consumer, worktrees en consumers y ADR gate git

- **Estado:** Aceptado — implementado 2026-10-29
- **Fecha:** 2026-10-29
- **Autor:** Juve + Claude
- **Cierra:** pendientes "fuera de alcance" de ADR-0073

---

## Contexto

ADR-0073 dejó cuatro pendientes:

1. `scripts/install-hooks.sh` hacía `jq '.hooks = $hooks'`: en cada sync diario reemplazaba TODO
   `.hooks` de `settings.local.json` y borraba los hooks que el consumer registró ahí.
2. `protect-skill-gate-bypass.sh` bloqueaba cualquier comando que mencionara el gate o el marker
   y tuviera un `>` en cualquier parte (ej. `cat … 2>/dev/null`) — falsos positivos reales en
   sesión — y a la vez no detectaba `rm`, `dd of=`, `python -c open(…)`, `find -delete`,
   `git checkout --`.
3. `.worktreeinclude` y `/worktrees/` en `.claude/.gitignore` eran un paso manual en consumers.
4. El ADR gate git (`commit-msg`) vive en `.git/hooks` (no versionado): nada lo instalaba en otros
   clones, y los consumers con el instalador viejo seguían con el pre-commit que leía el mensaje
   del commit ANTERIOR.

El plan pasó 5 rondas de validación adversarial. Hechos que cambiaron el diseño: los consumers de
`gitops-init` usan el **framework pre-commit** (`id: adr-gate`), para quienes nuestro instalador no
aplica; ningún hook del toolbox se ha borrado/renombrado nunca; la CLI de `check_adr_gate.py` es
estable en todo el historial.

## Decisión

### 1. `install-hooks.sh`: merge hook por hook, calculado antes de escribir
- Propios del harness = registrables (`# event:` + `# timeout:`; no librerías) ∪
  `manifest.registered[]` ∪ hooks que el manifest dice que el harness copió. Match por segmento
  de path `.claude/hooks/<nombre>` (absoluto, relativo o entre comillas; no `x.sh.bak`).
- Se quitan solo esos hooks — **hook por hook**, no el grupo (un hook del consumer puede
  compartir grupo con uno nuestro) — y se agregan los nuevos. Todo lo demás se preserva.
- El merge se calcula en la fase de plan: JSON inválido aborta antes de copiar nada; se escribe
  solo si cambió (sin backup ni reescritura diaria en vano); el dry-run reporta "preservados N".

### 2. Config de worktrees en consumers (decisión del usuario)
- `install-hooks` crea `.worktreeinclude` desde `templates/worktreeinclude.template` si falta y
  agrega `/worktrees/` a `.claude/.gitignore` (línea exacta, salto de línea garantizado, append
  O_APPEND). Nunca sobrescribe; no corre en un worktree enlazado.
- Si el usuario lo borra/quita después, queda **declinado** en el manifest (no se recrea cada día;
  `--force` lo recrea). El doctor deja de avisar en ese caso. Backup con `.gitignore.bak` y
  `CREATED.txt` para el rollback.

### 3. `protect-skill-gate-bypass.sh`: regla amplia + exenciones probadas
Se descartó un parser "solo destino por segmento": perdía casos que hoy se bloquean
(`xargs touch`, `F=…; touch $F`, `mv gate /tmp`). Se mantiene "menciona el archivo Y hay señal de
escritura" y solo se eximen destinos **probadamente seguros**: redirecciones a fd, `/dev/null` u
otra ruta literal (destino capturado respetando comillas; comilla sin cerrar → bloquea), `tee`/`cp`
hacia otra ruta. El resto usa verbos de escritura **en posición de comando** (inicio de línea o tras
`; & | ( $( \` { ! then do …`, con prefijos `sudo/env/nohup/timeout/VAR=`, rutas absolutas o `\verb`),
así `grep -ln`, `docker run --rm`, `git format-patch` no son falsos positivos. Se cierran `rm`,
`dd of=`, `python -c`, `find -delete`, `git checkout --`.

### 4. ADR gate git: `install_hooks.sh --check` como fuente única; auto-sync solo MIGRA
- `--check` (solo lectura, cabecera `# supports: --check`) devuelve 10 al día / 11 migración /
  12 falta / 13 no aplica (no es git, `core.hooksPath`, sin checker, o el framework gestiona el
  gate: commit-msg del framework, o pre-commit del framework + `id: adr-gate` no comentado). Un
  framework usado solo para lint NO es 13. Otro código = error (14 vía `trap ERR`).
- Una sola clasificación compartida por `--check` y la instalación (orden: entorno → rastros en
  pre-commit → framework → commit-msg); la instalación siempre limpia rastros en pre-commit (con
  `.bak`), sale 0 en éxito y en "no aplica", y hace preflight del checker (`--commit-message`).
- **auto-sync-daily** (subsistema 3b, flag `sync_git_hooks`): usa SIEMPRE el instalador del toolbox
  y solo si declara `# supports: --check` (uno viejo ignoraría el flag e instalaría). Solo actúa en
  11 (migración, con respaldo `.harness-backup-<ts>-githooks/`); 12 se loguea — **decisión del
  usuario**: instalar de golpe en un clon que nunca tuvo el gate encendería un bloqueo nuevo.
- **doctor** (sección 7) y **task-verify** avisan con 11/12 usando el mismo `--check` (toolbox o
  copia del repo, solo si soporta `--check`); **init.sh** lo instala en el onboarding de ai-tooling.

## Consecuencias

- Tests: `run-tests.sh` T7–T12 (merge, sin-cambios, JSON inválido, dry-run, `.worktreeinclude`,
  `.gitignore`), `bypass-gate-tests.sh` (60 casos), `githook-tests.sh` (36 casos). Suite completa:
  171 casos.
- Con `core.hooksPath` definido ya no se actualizan instalaciones en `.githooks/` (antes sí).

### Límites conocidos
- `id: adr-gate` editado a mano con `stages: [commit-msg]` y el framework solo en pre-commit → 13
  aunque el gate no corra.
- Config declarada sin framework + instalación manual del hook crudo + luego `pre-commit install`
  → el framework mueve el nuestro a `commit-msg.legacy` y el gate corre dos veces (el mensaje del
  código 12 lo advierte). Un gate viejo puede sobrevivir en `pre-commit.legacy`.
- Bypass-gate: globs/variables que nunca escriben el nombre, y editar `settings.json`. Es un badén:
  el gate también acepta el marker `<sid>-skill-loaded`.
- task-verify solo muestra el aviso cuando existe un scope.

## Referencias
- ADR-0072 (hardening del harness: reportar por default, escribir con intención, reversible)
- ADR-0073 (worktrees nativos — origen de estos pendientes)
- `templates/gitops/.pre-commit-config.yaml.template` (framework + nota de no duplicar el gate)
