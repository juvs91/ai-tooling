# Análisis exhaustivo: `deacero/commons` — instancia GitOps monorepo multi-lenguaje

**Fecha:** 2026-08-04
**Repo analizado:** `/Users/jeguzman/Documents/deacero/commons` (solo lectura, sin modificaciones)
**Contexto:** `commons` es una instancia real, bootstrapeada con `scripts/gitops-init.sh`, del
patrón GitOps monorepo trunk-based descrito en `.agents/skills/infrastructure/gitops-monorepo/SKILL.md`
de este repo (ai-tooling). Aloja 3 librerías compartidas (`python/auth`, `go/auth`, `typescript/auth`)
consumidas por otros repos de Grupo Deacero. Es el caso de uso real que valida el skill — y por
tener meses de uso en producción, ya generó fixes y aprendizajes que **no se han portado de vuelta**
a ai-tooling. Ese es el hallazgo central de este documento.

> **Actualización 2026-08-04 (mismo día):** los hallazgos de este documento se ejecutaron —
> ver §9 "Homologación ejecutada". §5 fue corregido tras verificación directa de código (ver
> nota ahí) — el análisis inicial tenía un error de hecho sobre `check_adr_gate.py`.

---

## 1. Qué es `commons` y cómo se relaciona con ai-tooling

`commons` **no es un fork** de ai-tooling ni depende de él en runtime. Es un repo hermano
independiente que:
- Usó `ai-tooling/scripts/gitops-init.sh` una vez para bootstrapear su `release.sh`, `CODEOWNERS`,
  `.pre-commit-config.yaml` y `.gitops-env`.
- Sincroniza sus skills desde `cornerstone-agents` (framework externo, `.agents/_repo/` es un
  clon crudo de `git@github.com:deagentic/cornerstone-agents.git`) — **no desde ai-tooling
  directamente**. El skill `gitops-monorepo` fue portado a mano, no vía ese sync.
- Desde entonces evolucionó de forma autónoma: tiene su propio historial de ADRs (4, numerados
  0007–0010, con colisiones de numeración frente a ai-tooling — ver §5) y su propio
  `ai-notes/AI_LEARNING.md` con hallazgos de sesiones reales.

Esto confirma que la instancia de referencia (`gitops-init.sh` + `release.sh` de ai-tooling)
funciona en producción real, pero también que **el modelo de "plantilla genérica → instancia
que evoluciona sola" no tiene canal de retorno**: los fixes de `commons` quedaron documentados
solo en su propio `AI_LEARNING.md`/ADRs, sin que ai-tooling se enterara.

---

## 2. Estructura completa de `commons`

```
commons/
├── .agents/
│   ├── _repo/                  ← clon crudo de cornerstone-agents (tiene su propio .git)
│   └── skills/                 ← 28 skills sincronizados + gitops-monorepo (portado a mano)
├── .claude/
│   ├── hooks/                  ← 5 hooks (vs 19 en ai-tooling, ver §4)
│   ├── sessions/                ← tracking de plan-gate
│   ├── settings.json
│   └── adr-gate.conf            ← config explícita, NO existe en ai-tooling (ver §3)
├── ai-notes/
│   └── AI_LEARNING.md           ← único archivo; sin subcarpeta analysis/
├── docs/adr/                    ← 4 ADRs: 0007, 0008, 0009, 0010 (sin index.md)
├── go/auth/                     ← módulo Go único (Makefile, go.mod, token.go, VERSION)
├── python/auth/                 ← paquete Python único (src/, tests/)
├── typescript/auth/             ← paquete npm único (src/, tests/)
├── scripts/
│   ├── gitops-init.sh (463 líneas)
│   └── release.sh (820 líneas)
├── tools/
│   ├── check_adr_gate.py (250 líneas)
│   └── install_hooks.sh (72 líneas)  ← sin tools/tests/
├── .cornerstone, .gitops-env, .pre-commit-config.yaml
├── CLAUDE.md (192 líneas), CODEOWNERS, README.md (138 líneas)
└── bitbucket-pipelines.yml (192 líneas)
```

**Organización de proyectos:** NO usa la convención `projects/<nombre>` (default de
`release.sh` sin `GITOPS_PROJECT_MAP`). Usa un `GITOPS_PROJECT_MAP` custom
(`python-auth:python/auth,go-auth:go/auth,typescript-auth:typescript/auth`) — organización
"por lenguaje primero", no "por proyecto primero". No hay `shared/libs/` materializado (un solo
proyecto por lenguaje aún no lo necesita), aunque `release.sh` conserva la lógica de detección
de shared deps (`shared_deps_of()`) por si se agrega en el futuro.

---

## 3. `scripts/release.sh` — comparación literal contra ai-tooling

`wc -l`: commons = 820, ai-tooling = 838. Diff hecho línea por línea.

**Gotchas conocidos — cobertura idéntica, código byte a byte igual:** macOS `grep -oP` (usa
`grep -o` POSIX), shallow clone (`require_full_history`), cherry-pick detection vía `git cherry`
(`is_applied_to()`), sort numérico de `hotfix.N`, `git fetch --tags` antes de check de tag
existente, `git ls-tree` para sparse, `sed -i.bak` portable macOS/GNU. Ningún gotcha de la tabla
del SKILL.md tiene implementación divergente entre los dos repos.

**Única divergencia funcional real: el subsistema `worktree`.**

| | commons | ai-tooling |
|---|---|---|
| Subcomandos | `add`, `drop`, `list` | `add`, `add-branch`, `rm`/`remove`, `prune`, `clean`, `list` |
| Ruta del worktree | fija: `../<repo>-<proyecto>/` | parametrizable (`add-branch <rama> [<path>] [<base>]`) |
| Sparse dentro del nuevo worktree | **se aplica automáticamente** (scripts/ + dir del proyecto) | **no se aplica** — hereda checkout completo |
| Detección de huérfanos en `status` | no | sí (`git worktree prune --dry-run`) |
| Alias `wt` | sí | no |
| ADR de referencia | ninguno explícito en código (solo `# ── git worktrees ──`) | ADR-0044 (comentario `Ref: ADR-0008` en código — ver nota de numeración abajo) |

Esta divergencia **ya está documentada y aceptada como deuda técnica** en el propio `commons`:
`docs/adr/ADR-0008-reconciliacion-esquema-tags.md` dice explícitamente en su sección
"Fuera de alcance": *"No se homologa el subsistema de worktrees... Traer ese modelo a commons
queda como mejora futura"*. Y `ai-notes/AI_LEARNING.md` de commons (líneas 141-150) tiene el
TODO técnico correspondiente. No es un hallazgo nuevo — es deuda ya conocida por el equipo de
commons, simplemente nunca se cerró en ningún lado.

**Hallazgo con dirección inversa (mejora de commons ausente en ai-tooling):** el `cmd_worktree_add`
de commons aplica sparse-checkout automáticamente al crear el worktree (líneas ~716-722). El
`cmd_worktree add`/`add-branch` de ai-tooling **no tiene esa lógica** — un worktree nuevo en
ai-tooling siempre nace con checkout completo, y el usuario debe correr `release.sh work`
manualmente después si quiere sparse. Confirmado con grep directo: no hay llamadas a
`sparse-checkout` dentro del rango de líneas de `cmd_worktree*` en `ai-tooling/scripts/release.sh`.
**Candidato concreto de back-port.**

**Diferencia cosmética/de personalización (no bug):** `resolve_remote()` en commons agrega
`deacero` como primera opción de auto-detect antes de `origin`/`upstream` — correcto para una
instancia real, no aplica a la plantilla genérica de ai-tooling.

---

## 4. `.claude/hooks/` — comparación

commons tiene 5 hooks, ai-tooling tiene 19. Los 5 compartidos (`adr-gate.sh`,
`block-dangerous.sh`, `config-protection.sh`, `plan-mode-gate.sh`, `protect-secrets.sh`) tienen
contenido divergente en 3 de los 5:

- **`adr-gate.sh`**: commons (124 líneas) carece del bloque de enforcement de numeración
  secuencial de ADRs que sí tiene ai-tooling (líneas 39-73), y carece de la validación de que
  el ADR nuevo mencione la razón/prefijo protegido específico. En commons, cualquier ADR nuevo
  sin relación con el cambio (trabajo previo sin commitear) bypasea el gate permanentemente.
  Gap real de robustez, aunque de riesgo bajo (bypass requiere ya tener un ADR staged).
- **`block-dangerous.sh`**: la regla 5 (protección de worktree activo) en ai-tooling segmenta el
  comando por `&&`/`||`/`;`/`|` antes de buscar el path del worktree; commons hace un
  `grep -qF` simple sobre el comando completo — riesgo de falso positivo en comandos compuestos
  en commons, no en ai-tooling.
- **`config-protection.sh`**: divergencia **intencional**, no bug — ai-tooling protege archivos
  de linter/CI genéricos; commons protege `CODEOWNERS`/`.gitops-env` (gobernanza GitOps
  específica). Ambos son correctos para su contexto.

`plan-mode-gate.sh` y `protect-secrets.sh` son idénticos byte a byte en ambos repos.

Los 14 hooks exclusivos de ai-tooling (scope-gate, intent-bootstrap, quality-enforce,
ts-quality-gate, worktree-isolation-gate, etc.) son parte del framework más amplio de
task-scope/quality que commons no adoptó — esperado, dado que commons es una librería
compartida simple, no un proyecto con el mismo nivel de tooling de desarrollo que ai-tooling.

---

## 5. `tools/check_adr_gate.py` — corrección de hallazgo (verificado directamente en código)

> **Corrección (2026-08-04):** el análisis inicial de esta sección afirmaba que "ai-tooling
> sigue con `GUARDED_PATTERNS`/`fnmatch` hardcodeado" y que solo `commons` soportaba
> `.claude/adr-gate.conf`. **Eso era incorrecto** — verificado leyendo directamente
> `ai-tooling/tools/check_adr_gate.py` línea por línea: ya tiene `_load_conf_rules()`, que lee
> `.claude/adr-gate.conf` con el mismo algoritmo `startswith`+`regex` que `commons`, con
> `GUARDED_PATTERNS` como fallback solo si el archivo no existe. `ai-tooling` simplemente nunca
> materializó su propio `.claude/adr-gate.conf` (usa el fallback, que da el mismo resultado).
> La lección: un reporte de sub-agente no es sustituto de leer el código real antes de actuar
> sobre un hallazgo — este documento se corrigió tras esa verificación, antes de portar nada.

Ambos repos bifurcaron tras `ADR-0009` (commons) / `ADR-0035` (ai-tooling) — mismo título,
mismo propósito ("unificación de fuente de verdad del ADR gate"), redactados de forma
independiente en cada repo. La única brecha real y confirmada:

**Solo en ai-tooling (commons no lo tenía — portado en la homologación, ver §9):**
- `_check_adr_sequence()` — valida que el ADR nuevo use `max(existentes)+1` sin huecos.
- `_staged_files()` — fallback a `git diff --cached --name-only` cuando se invoca sin
  `--changed-files` (caso pre-commit con `pass_filenames: false`).
- Manejo explícito de `SKIP_TOKEN` combinado con `sequence_errors`.

**Inconsistencia adicional encontrada en commons al portar lo anterior (no reportada en el
análisis inicial):** el `.py` de `commons` ignoraba la directiva `adr_path:` de
`.claude/adr-gate.conf` (la parseaba y descartaba sin usarla) — solo el hook bash
(`adr-gate.sh`) la respetaba. Si alguien cambiaba `adr_path:` en el conf, el hook bash y el
`.py` habrían quedado mirando directorios de ADR distintos. Corregido en la misma homologación.

`install_hooks.sh`: diff es solo reordenamiento de bloques idénticos, sin diferencia funcional.

---

## 6. `.pre-commit-config.yaml` de commons — hallazgo no resuelto

El filtro `files: '(?x)^(\.agents/skills/.*\.md)$'` del hook `adr-gate` en el framework
`pre-commit` **no protege ninguna de las 5 rutas reales que CLAUDE.md de commons dice que
protege** (`scripts/release.sh`, `scripts/gitops-init.sh`, `bitbucket-pipelines.yml`,
`tools/check_adr_gate.py`, `tools/install_hooks.sh`). Es el mismo defecto que `ADR-0009`
corrigió en `check_adr_gate.py`, pero `ADR-0009` explícitamente lo dejó fuera de alcance
("el mecanismo de enforcement elegido para commons es el hook crudo de
`tools/install_hooks.sh`, no el framework pre-commit"). **Riesgo latente:** si alguien en
commons activa `pre-commit install` en vez de `bash tools/install_hooks.sh`, el gate de ADR
en ese framework sigue roto silenciosamente — protege un path (`.agents/skills/`) que en la
práctica ya lo cubre el hook de Claude Code, pero no protege los 5 paths reales de GitOps.
No es un problema de ai-tooling (no usa ese mismo framework de la misma forma), pero vale la
pena que quien mantenga commons lo sepa.

---

## 7. ADRs — colisión de numeración y correspondencias

| commons | ai-tooling equivalente | Nota |
|---|---|---|
| ADR-0007-gitops-monorepo-trunk-based.md | ADR-0007-gitops-monorepo-trunk-based.md | Mismo título, contenido adaptado a Deacero vs. plantilla genérica ("Company") |
| ADR-0008-reconciliacion-esquema-tags.md | *(sin equivalente directo — colisión de número con ADR-0008-plan-lock-implicit-exit-signal4.md, dominio Proxy, sin relación; renumerado a ADR-0044 en ai-tooling por esta razón)* | Fuente primaria de la deuda técnica de worktrees (§3) |
| ADR-0009-unificacion-fuente-verdad-adr-gate.md | ADR-0035-unificacion-fuente-verdad-adr-gate.md | Mismo título, bifurcación real de contenido (§5) |
| ADR-0010-manifiesto-fuente-de-verdad-version.md | ADR-0034-manifiesto-fuente-de-verdad-version.md | ADR-0034 de ai-tooling reconoce explícitamente que el hallazgo se originó en commons como ADR-0010 |
| — | ADR-0044-worktree-gitops-integration.md (Estado: **Propuesto**, no Aceptado) | Sin versión adaptada en commons — describe exactamente la mejora que commons necesita (§3) |

La colisión de numeración (ADR-0008 con dos significados distintos entre repos) confirma que
no hay ningún mecanismo de sincronización de numeración entre repos hermanos — es puramente
por convención local de cada uno. No es un bug per se, pero es una trampa si algún día se
migran ADRs entre repos sin renumerar (como ya tuvo que hacerse una vez, según la nota en
ADR-0044).

---

## 8. Gotchas encontrados en producción real (commons) ausentes en la tabla de ai-tooling

La tabla de gotchas de `ai-tooling/.agents/skills/infrastructure/gitops-monorepo/SKILL.md`
tiene 12 filas. La de `commons` tiene 13 — **2 gotchas reales, encontrados en uso real, no están
documentados en ai-tooling:**

1. **Bash de sistema en macOS (3.2) no soporta `mapfile`/`readarray`.** Encontrado al adaptar
   `.agents/sync_skills.sh` (copiado de otro repo, `wpc-backend`). No afecta a `release.sh`
   (no usa `mapfile`), pero sí a cualquier script bash nuevo que alguien escriba asumiendo
   bash ≥4. Documentado en `AI_LEARNING.md` de commons, líneas 53-54.
2. **`adr_path: docs/adr` en `.claude/adr-gate.conf` + `tr -d '/'` en `adr-gate.sh` borraba
   el `/` interno** (→ `docsadr`, directorio inexistente) → el gate bloqueaba SIEMPRE aunque
   hubiera un ADR nuevo staged. Fix aplicado en commons: `sed 's:/*$::'` (solo quita `/` final,
   preserva los internos). **Verificado directamente contra `ai-tooling/.claude/hooks/adr-gate.sh:36`
   — ai-tooling YA usa `sed 's:/*$::'`, no tiene el bug.** Es decir: este fix probablemente se
   portó a ai-tooling por otra vía (o nunca tuvo el bug porque no usó `tr -d '/'` originalmente),
   pero el gotcha en sí **no está documentado en la tabla del SKILL.md** — vale la pena
   agregarlo igual, como advertencia para quien construya un `adr-gate.conf` custom desde cero.

**Fila que solo tiene ai-tooling, ausente en commons:** `GITOPS_PROJECT_MAP` con `.` (raíz) →
`work` en cone mode incluye solo top-level → usar `expand`. No aplica a commons porque ningún
proyecto de commons vive en la raíz.

---

## 9. Homologación ejecutada (2026-08-04)

Todo lo que valía la pena portar de este análisis **ya se portó, en ambas direcciones**, en la
misma sesión en la que se escribió este documento. No quedan recomendaciones abiertas de
código — lo que queda son ítems concretos de piloto/prueba en uso real (ver "A pilotear" al
final de esta sección).

### Antes → después del subsistema `worktree`

```
ANTES (2026-08-04, mañana)
┌─────────────────────────────┐        ┌──────────────────────────────────┐
│ ai-tooling/scripts/release.sh│        │ commons/scripts/release.sh        │
│                              │        │                                  │
│ cmd_worktree()               │        │ cmd_worktree_add()   (por proy.) │
│   add | add-branch | rm |    │        │ cmd_worktree_drop()  (por proy.) │
│   prune | clean | list       │        │ cmd_worktree_list()              │
│                              │        │   └─ auto-sparse por proyecto ✔  │
│   └─ sin auto-sparse ✘       │        │                                  │
│                              │        │ block-dangerous.sh regla 5:      │
│ block-dangerous.sh regla 5:  │        │   grep simple (falso positivo    │
│   segmentado &&/||/;/| ✔     │        │   en comandos compuestos) ✘      │
│                              │        │                                  │
│ check_adr_gate.py:           │        │ check_adr_gate.py:               │
│   + _check_adr_sequence ✔    │        │   sin _check_adr_sequence ✘      │
│   + _staged_files ✔          │        │   ignora directiva adr_path: ✘   │
└─────────────────────────────┘        └──────────────────────────────────┘
        divergieron desde el bootstrap, sin canal de retorno (ver §1)

DESPUÉS (2026-08-04, misma sesión — ADR-0048 en ai-tooling, ADR-0011 en commons)
┌─────────────────────────────────────────────────────────────────────────┐
│           cmd_worktree()  —  BYTE-IDÉNTICO en ambos repos                │
│           add | add-branch | rm | prune | clean | list                  │
│           └─ _replicate_sparse_checkout() en add/add-branch  ✔ (ambos)  │
│                                                                          │
│           block-dangerous.sh regla 5 — segmentado &&/||/;/|  ✔ (ambos)  │
│           worktree-isolation-gate.sh — nuevo en ambos         ✔ (ambos) │
│                                                                          │
│           check_adr_gate.py                                            │
│             _check_adr_sequence + _staged_files  ✔ (ambos)             │
│             adr_path: respetado por .py y .sh    ✔ (ambos)             │
└─────────────────────────────────────────────────────────────────────────┘
   único diff restante: @deacero vs @company, orden de resolve_remote()
   (ambos documentados y esperados — ver §3)
```

### El canal de retorno que no existía

```
                    ┌────────────────────────┐
                    │   cornerstone-agents    │  (framework externo,
                    │   (github: deagentic)   │   sync unidireccional)
                    └───────────┬────────────┘
                                │ sync_skills.sh
                                ▼
   ┌───────────────┐   gitops-init.sh    ┌───────────────┐
   │  ai-tooling    │ ──────(bootstrap,──▶│    commons     │
   │  (plantilla)   │        una vez)     │  (instancia    │
   │                │                     │   producción)  │
   │                │  ✘ sin canal de     │                │
   │                │     retorno hasta   │  ADR-0008/0009/│
   │                │     hoy             │  0010: fixes   │
   │                │                     │  reales sin    │
   │                │                     │  portar        │
   └───────────────┘                     └───────────────┘
           │                                      │
           │        2026-08-04: primer port       │
           │◀─────────── bidireccional ───────────▶│
           │   ADR-0048 (ai-tooling) ⇄ ADR-0011 (commons)
           ▼                                      ▼
   2 gotchas de producción real         cmd_worktree unificado +
   entran a SKILL.md de referencia      check_adr_gate.py mejorado
```

### Checklist de lo portado

**`ai-tooling` (`docs/adr/ADR-0048-homologacion-gitops-monorepo-commons.md`):**
- [x] `_replicate_sparse_checkout()` en `cmd_worktree add`/`add-branch` (generalización del
  auto-sparse-por-proyecto que ya tenía `commons`, adaptado al modelo rama-céntrico)
- [x] 2 gotchas de producción real agregados a la tabla de `SKILL.md` (bash 3.2 `mapfile`,
  bug `tr -d '/'` en `adr_path`)
- [x] `ADR-0044` aceptado (estaba "Propuesto" pese a estar ya implementado)
- [x] **Hallazgo adicional, no reportado en el análisis inicial:** bug de `set -e` + `&&` sin
  guarda (`[[ $found -eq 0 ]] && ok "..."` como única sentencia, sin `|| true`) en
  `cmd_worktree clean` y `cmd_check` — con `set -euo pipefail` activo, el script abortaba
  silenciosamente cada vez que SÍ había un candidato a limpiar (el caso útil), saltándose los
  mensajes de ayuda subsiguientes. Reproducido y corregido en sandbox antes de tocar el
  archivo real (ver Fase 0 en el plan de esta sesión).

**`commons` (`docs/adr/ADR-0011-homologacion-worktree-subsystem.md`):**
- [x] `cmd_worktree()` unificado (`add|add-branch|rm|prune|clean|list`), reemplazando
  `cmd_worktree_add/drop/list` (proyecto-céntrico)
- [x] `cmd_status()` con listado de worktrees + detección de huérfanos
- [x] `block-dangerous.sh` regla 5 segmentada por `&&`/`||`/`;`/`|`
- [x] `worktree-isolation-gate.sh` (hook nuevo) + registrado en `.claude/settings.json`
- [x] `check_adr_gate.py`: `_check_adr_sequence()` + `_staged_files()` portados; directiva
  `adr_path:` ahora respetada también por el `.py` (antes solo el hook bash la leía)
- [x] `SKILL.md` y `CLAUDE.md` actualizados a la nueva interfaz de `worktree`
- [x] mismo fix de `set -e` aplicado (el bug era idéntico en ambos repos)

### Validación aplicada (resumen — detalle completo en el plan de la sesión)

Sandbox aislado (7 escenarios) → clon temporal de `ai-tooling` real (mismos 7 escenarios,
confirmando integración con `trunk_branch()`/`resolve_remote()`/`require_arg()` reales) → diff
explícito línea por línea antes de escribir en `commons` real. En `commons` no se ejecutó
`worktree add`/`add-branch` en vivo — el código ya estaba doblemente validado y crear
worktrees reales en un repo de producción sin supervisión directa del usuario no se
justificaba.

### A pilotear (no son recomendaciones abiertas — son pasos de adopción concretos)

1. **Correr `./scripts/release.sh worktree add-branch <rama> <path>` real en `commons`** —
   el código está validado (sandbox + clon de `ai-tooling`), pero nunca se ejecutó dentro del
   `commons` real. Hacerlo una vez, en un escenario cotidiano, antes de que el resto del
   equipo lo adopte.
2. **Confirmar que `pre-commit install` en `commons` sigue funcionando** tras el cambio a
   `tools/check_adr_gate.py` (el hook de `pre-commit` invoca ese mismo script).
3. **Confirmar en la próxima release real de una librería** (`python-auth`/`go-auth`/
   `typescript-auth`) que `bitbucket-pipelines.yml` no se vio afectado — no debería (no se
   tocó ningún archivo de CI), pero es la primera release después de este cambio.
4. **Vigilar el `.adr-gate-bypasses.log`** de `commons` las primeras semanas — la nueva
  validación de secuencia de ADRs (`_check_adr_sequence`) es más estricta que antes; si genera
  fricción real (dos personas creando ADRs en paralelo), decidir si se ajusta o se documenta
  el flujo de "quien llega primero numera".

---

## 10. Qué NO fue revisado, y por qué

- **Contenido completo de `.agents/skills/` compartidos** (los 14 skills con mismo path en
  ambos repos) — solo se comparó *presencia*, no diff de contenido byte a byte, porque
  `commons` los sincroniza desde `cornerstone-agents` (fuente externa, no ai-tooling) y un
  diff de contenido no sería atribuible a "deuda con ai-tooling" sino a drift del framework
  externo, fuera del alcance de esta tarea.
- **Código fuente de las 3 librerías** (`go/auth`, `python/auth`, `typescript/auth`) — el
  pedido fue analizar la implementación GitOps, no el código de negocio de las librerías.
- **`.agents/_repo/` (clon de `cornerstone-agents`)** — es un submódulo/clon de un repo externo
  a Deacero; no se inspeccionó su contenido interno más allá de confirmar que existe.
- **Historial de git de `commons`** (commits, branches activas, tags reales en el remote) —
  el análisis fue de archivos en disco (working tree), no del estado del remote Bitbucket;
  no se corrió `git fetch`/`git log` contra el remote real de Deacero.
- **`.claude/settings.json` y `.vscode/settings.json` de commons** — mencionados en la
  estructura pero no diffeados línea por línea contra los de ai-tooling (bajo impacto GitOps).
- **Verificación en vivo de los 2 gotchas de macOS/bash** — se tomó como hecho lo documentado
  en `AI_LEARNING.md` de commons; no se reprodujo el bug de `tr -d '/'` corriendo el hook
  original en un sandbox (sí se verificó, en cambio, que `ai-tooling/.claude/hooks/adr-gate.sh`
  actual usa la versión ya corregida — ver §8).
