# ADR-0073: Worktrees nativos de Claude Code en el harness

- **Estado:** Aceptado — implementado 2026-10-29
- **Fecha:** 2026-10-29
- **Autor:** Juve + Claude
- **Enmienda:** ADR-0044 (integración de worktrees al GitOps monorepo)

---

## Contexto

ADR-0044 cubrió los worktrees **manuales** (`release.sh worktree add`). Claude Code crea además
worktrees **nativos** — `EnterWorktree`, `claude --worktree`, y subagentes / `agent()` de
Workflow con `isolation: 'worktree'` — en `<repo>/.claude/worktrees/<nombre>/`. El harness no
los contemplaba. El plan se validó en 9 rondas (doc oficial, lectura del repo, revisores
adversariales, experimentos git 2.56) y una verificación empírica en vivo (Fase 0).

### Hechos verificados (Fase 0, 2026-10-29, Claude Code 2.1.312)

| # | Pregunta | Resultado |
|---|----------|-----------|
| a | cwd del PROCESO hook | Sigue al `cd` del Bash tool; en un subagente aislado es su worktree. Al inicio puede ser la ruta del symlink (`/case-sensitive/...`) mientras `.cwd` es la física (`/Volumes/...`) |
| b | `$CLAUDE_PROJECT_DIR` dentro de un worktree | Se queda en la raíz principal |
| c | `isolation` en `tool_input` de Agent | Sí llega (`"worktree"`) |
| d | session id del subagente | Igual al del padre |
| e | origen de los hooks en un worktree | `settings*.json` del checkout principal (el worktree no tiene `settings.local.json`) |
| g | discriminador de entorno principal vs subagente | **No existe**: el hilo principal también tiene `CLAUDE_CODE_CHILD_SESSION=1` y `AI_AGENT=…_agent` |
| h | `agent_id` en eventos del subagente | Sí (`agent_id`, `agent_type`); ausente en el hilo principal |

Además, de la documentación oficial: el stderr de un PreToolUse con exit 0 **nunca** llega al
modelo; `additionalContext` sí; `permissionDecision: "allow"` **salta el prompt de permisos**.
`CLAUDE_PROJECT_DIR` no existe en el entorno del Bash tool (sí `CLAUDE_CODE_SESSION_ID`).

### Hallazgos

| # | Defecto | Evidencia |
|---|---------|-----------|
| H1 | `.claude/worktrees/` no ignorado: `git add -A` agrega un gitlink de repo embebido | `git check-ignore` vacío |
| H2 | Sin `.worktreeinclude`: worktrees sin `.env`, `profile-envs/`, `settings.local.json` | doc + `git status --ignored` |
| H3 | Hooks con estado en `.cwd`: tras un `cd` crean `.claude/` basura en subdirectorios — llegó a commitearse | `vendor/hlwss/.claude/{.gitignore,drift-state/,task-scope.json}` (eliminados) ; reproducido 2 veces en esta sesión |
| H4 | 3 estrategias de raíz en hooks de sesión; markers partidos. **Reproducido en vivo:** tras `cd scripts`, `skill-load-gate` bloqueó `Agent` porque buscaba el marker en `scripts/.claude/sessions` | Fase 0 |
| H5 | `scope-gate`: en `.claude/worktrees/<n>` bloqueaba trabajo legítimo (analysis/synthesize); en un worktree hermano fallaba abierto | — |
| H6 | `adr-gate` / `protect-secrets` resolvían el repo por el cwd del proceso: dentro de un worktree anidado el gate de ADR fallaba abierto y (con H1 resuelto) el escaneo de secretos se saltaba por `check-ignore` | — |
| H7 | `sync_skills.sh` resolvía su raíz por el cwd: dentro de un worktree sobrescribía `.agents/skills/` **trackeados** de esa rama | P015 en AI_LEARNING |
| H8 | `task-verify.sh` en un worktree no encontraba el scope y pasaba en silencio | — |
| H9 | `worktree-isolation-gate`: el caso Agent afirmaba que Agent no soporta `isolation` (falso); ningún aviso llegaba al modelo (stderr + exit 0) | doc + Fase 0 (c) |
| H10 | `tools/install_hooks.sh` exigía `.git/hooks` (en un worktree `.git` es archivo); el pre-commit leía `COMMIT_EDITMSG` del commit **anterior**; un pre-commit ajeno se sobrescribía en la 2.ª corrida | — |
| H11 | `adr-gate.sh` con `set -euo pipefail`: sin ADRs nuevos el `grep` sale 1, la asignación mata el hook con **exit 1** (error no bloqueante) → el gate **fallaba abierto en el caso que debía bloquear**. Preexistente, independiente de worktrees; oculto porque casi siempre hay algún ADR sin commitear | test W7 |
| H12 | `release.sh` leía `.git/shallow` y `.git/CHERRY_PICK_HEAD` relativos (rotos en worktrees y subdirs); `worktree clean` marcaba como "mergeado" todo worktree recién creado | — |

## Decisión

**No crear hooks `WorktreeCreate` / `WorktreeRemove`.** `WorktreeCreate` reemplaza la creación
nativa (si falla, la sesión sale con 1), desactiva `.worktreeinclude`, y vía `install-hooks` +
`auto-sync-daily` se propagaría a todos los consumers sin opt-out por hook. Sin `WorktreeCreate`,
Claude Code ya hace `git worktree remove --force` al limpiar. En su lugar: configuración nativa
+ hooks/scripts conscientes de worktrees.

### Principios

1. **Raíz del estado = `state_root`** (lib): `CLAUDE_PROJECT_DIR` → toplevel → cwd, mapeada fuera
   de un worktree enlazado (`M + rel(base, W)`, conserva subproyectos) y canonizada.
2. **Acciones sobre el archivo editado usan el repo del archivo** (`repo_of_file`).
3. **Worktree enlazado ⇔ `--git-dir` ≠ `--git-common-dir`** — clasifica bien submódulos y
   `--separate-git-dir` (la primera entrada de `worktree list` no es el checkout en esos layouts).
4. **Todo se canoniza (`pwd -P`) antes de comparar** — git imprime paths físicos y este equipo
   tiene doble montaje `/case-sensitive` → `/Volumes/case-sensitive`.
5. **Compatibilidad con consumers en versiones mixtas** (install-hooks salta archivos
   modificados localmente): `declare -F` + fallback; `project_root` conserva su salida exacta
   fuera de worktrees.

### Cambios

- **Lib** `.claude/hooks/task-scope-lib.sh`: `canon_path`, `git_info` (un fork; detecta git
  < 2.31 por eco de `--path-format`), `is_linked_worktree`, `main_worktree_of`, `map_to_main`,
  `state_root`, `repo_of_file`, `scope_locate` (scope por session id desde Bash: subir, `find`
  acotado a profundidad 5, legacy). Todas retornan 0 (seguras bajo `set -e`).
- **Estado de sesión** en `state_root`: `skill-autoload`, `plan-mode-gate`, `track-skill-load`,
  `edit-drift-detector` (contador por session id), `quality-enforce` + `ts-quality-gate` (clave
  compartida relativa a la raíz; tsc del `tsconfig.json` más cercano al archivo, binario local o
  PATH — nunca `npx`; tsc roto sin diagnósticos no borra estado; match anclado).
  `skill-load-gate.sh` lee el marker de la raíz o del cwd (lo edita el humano:
  `protect-skill-gate-bypass.sh` bloquea al agente). `track-skill-load` escribe además el marker
  relativo durante una release si ese `.claude/.gitignore` ya ignora `sessions/` (transición).
- **Registro `<sid>-main-top`**: `track-skill-load` (PostToolUse, solo eventos sin `agent_id`)
  registra el toplevel del hilo principal, con cache de `.cwd` en `$TMPDIR/claude-$UID/` (sin
  forks cuando no cambia). `task-verify.sh` solo borra el scope si el toplevel actual coincide;
  sin registro, solo fuera de un worktree enlazado. Evita que un subagente aislado (mismo
  session id) cierre el scope del padre.
- **Repo del archivo**: `scope-gate` (jurisdicción = mismo git common-dir; relativo al worktree
  conservando el offset de subproyecto; ROOT canonizado antes de construir el path del scope),
  `adr-gate` (+ config desde el repo del archivo, regex de ADR nuevo `^(\?\?|A.)` acepta `AM`,
  fix H11), `protect-secrets`.
- **Guards de escritura**: `sync_skills.sh` (raíz por `BASH_SOURCE`, sale en worktree enlazado);
  `auto-sync-daily.sh` (sale en worktree enlazado; invoca `(cd "$REPO_ROOT" && bash
  .agents/sync_skills.sh)` — única vía para corregir consumers, cuya copia de `sync_skills.sh`
  no se actualiza); hook UserPromptSubmit en `settings.json` con `cd "$CLAUDE_PROJECT_DIR"`.
- **`worktree-isolation-gate`**: solo Workflow; aviso por `additionalContext` **sin**
  `permissionDecision` (con `allow` auto-aprobaría el Workflow); cuenta `agent(` vs
  `isolation: 'worktree'`.
- **`block-dangerous`** regla 5 con `realpath`; **`_cc-lib.sh`** `-e .git`;
  **`release.sh`** `--git-path`, path default de worktree relativo al principal, `clean` solo
  ramas con ≥1 commit propio (reflog) ya mergeadas.
- **`tools/install_hooks.sh`**: `--git-path hooks`, gate en **commit-msg** (mensaje real en `$1`),
  migra instalaciones en pre-commit, preserva hooks ajenos, no instala con `core.hooksPath`
  global/husky.
- **`harness-doctor.sh`** sección 6: `.worktreeinclude`, huérfanos, conteo (>4), ramas mergeadas
  con commit propio (sin fetch).
- **Config nativa**: `.claude/.gitignore` → `/worktrees/`; `.worktreeinclude`; `PRUNE` de
  `intent-bootstrap` excluye `.claude/worktrees`.
- **Skills** (`.agents/skills/`): `infrastructure/gitops-monorepo/SKILL.md` (worktrees nativos) y
  `frontend-analysis/SKILL.md` (se quita la afirmación falsa sobre Agent).

## Consecuencias

- Tests: `tests/harness/worktree-tests.sh` (38 casos, fixtures git reales con worktree anidado,
  hermano, symlink de doble montaje y repo ajeno), invocado por `run-tests.sh`.
- `ts-quality-gate` se enciende en monorepos sin `tsconfig.json` en la raíz; un tsconfig de
  solución (`files: []` + `references`) no verifica nada.
- En una sesión de subproyecto, `sync_skills.sh` sincroniza la raíz del repo donde vive el script.

### Límites conocidos

- Numeración de ADRs por worktree: dos worktrees en paralelo pueden aceptar el mismo número.
- `worktree clean`/doctor no detectan squash-merges; sin reflog de la rama no se listan.
- `.claude/settings.local.json` se copia una sola vez al crear el worktree (best effort).
- El proxy/docker solo desde el checkout principal (`container_name` y puerto 8083 fijos).
- Un submódulo dentro de un worktree no se mapea al principal.
- Un subagente **no aislado** comparte session id y cwd del padre: puede borrar su scope (ya
  ocurría antes).
- `cd W && ./scripts/task-verify.sh` en un solo comando lee un `main-top` viejo y conserva el
  scope (dirección segura).
- `ExitWorktree` con `discard_changes: true` lo bloquea `block-dangerous.sh` por diseño: hacer
  commit y luego `remove`.

### Fuera de alcance (hallazgos registrados)

- `install-hooks.sh` (`.hooks = $hooks`) borra hooks propios del consumer en `settings.local.json`.
- `protect-skill-gate-bypass.sh` da falso positivo con cualquier `>` (ej. `2>/dev/null`) si el
  comando menciona el archivo protegido.
- Deriva de CLAUDE.md (versión de `block-no-verify`, hooks UserPromptSubmit no listados) y del
  índice de ADRs (0062–0071 faltan, 0060 duplicado).
- `session-audit.sh` (slug), symlinks globales de `init.sh`/`ralph-install.sh`, `cd` sugerido por
  `migration-gate`, `.venv-*` del proxy sin ignorar.
- `gitops-init.sh` no actualiza `tools/install_hooks.sh` en consumers existentes.

## Referencias

- ADR-0041 (scope por sesión), ADR-0044 (worktrees GitOps), ADR-0047 (auto-sync), ADR-0053
  (block-dangerous regla 5), ADR-0072 (hardening del harness)
- https://code.claude.com/docs/en/hooks.md (WorktreeCreate/Remove, additionalContext,
  permissionDecision, CLAUDE_PROJECT_DIR) · https://code.claude.com/docs/en/worktrees.md
  (`.worktreeinclude`) · https://code.claude.com/docs/en/settings-reference.md (`worktree.*`)
