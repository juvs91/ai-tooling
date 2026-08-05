# ADR-0034: El manifiesto de cada proyecto es la fuente de verdad de la versión

- **Estado:** Aceptado
- **Fecha:** 2026-08-02
- **Autor:** jeguzman

---

## Contexto

`ai-tooling` es el repo origen de `scripts/release.sh` — el resto de repos GitOps de
Deacero (ej. `commons`) lo obtienen vía `scripts/gitops-init.sh` y lo mantienen como
"contrato compartido" (verificado byte a byte salvo la implementación de `worktree`, que
diverge intencionalmente por repo).

Una sesión de trabajo en `commons` encontró que ningún flujo de publish comitea la versión
real de vuelta al manifiesto de cada librería (`pyproject.toml` / `package.json` / `VERSION`):
el step de publish de un tag la extraía directamente del tag y modificaba el manifiesto solo
en el checkout efímero del CI, y `release.sh tag <proyecto> <version>` tampoco la escribía —
la versión vivía únicamente como argumento de línea de comandos, sin relación con lo que hay
en el repo. Resultado: git y el registro de artefactos nunca están de acuerdo en "cuál es la
versión actual" fuera de revisar los tags o el registro directamente, y los builds `-dev.N`
salían siempre desde la misma versión base porque nada actualizaba el manifiesto. Esa
decisión quedó documentada en `commons` como `docs/adr/ADR-0010-manifiesto-fuente-de-verdad-version.md`.

Como `scripts/release.sh` de `ai-tooling` es el origen de ese mismo contrato, la corrección
se replicó aquí — pero sin ADR propio, a pesar de que este repo es la fuente que otros
bootstrapean. Este ADR cierra ese hueco.

---

## Decisión

El manifiesto de cada proyecto pasa a ser la **única fuente de verdad** de la versión.
Se separan dos responsabilidades que antes vivían mezcladas en el argumento `<version>` de
`release.sh tag`:

1. **`release.sh bump <proyecto> <version>`** (nuevo) — único comando que escribe el
   manifiesto (detecta `pyproject.toml`/`package.json`/`VERSION` sin hardcodear nombres de
   proyecto). Es una edición de archivo más: se comitea y revisa por PR como cualquier otro
   cambio de código, sin requerir estar en trunk ni pushear nada.
2. **`release.sh tag <proyecto> [version-esperada]`** (cambia de firma) — ya no recibe la
   versión como argumento obligatorio. La lee del manifiesto (que ya llegó a trunk vía el PR
   de `bump`) y taguea exactamente esa versión. El argumento opcional `version-esperada` es
   solo una verificación de seguridad: si se pasa y no coincide con el manifiesto, el comando
   falla explícitamente en vez de taguear una versión distinta a la esperada.

Cadena de consistencia resultante: `bump` fija la versión (PR revisado) → `tag` la lee de ahí
y crea `<proyecto>@<version>` → el step de publish (sin cambios) parsea esa misma versión del
tag y publica exactamente eso.

Durante la implementación se corrigieron dos bugs encontrados al probar el código (no en el
diseño original):

- El primer borrador de `cmd_tag` tenía `require_semver "$version" || die "..."`, que nunca
  se ejecutaría — `require_semver()` ya termina el proceso con `die` internamente si la
  versión es inválida. Se corrigió con una verificación inline de formato semver sobre la
  versión leída del manifiesto.
- `sed -i "..."` sin sufijo de backup falla en el `sed` de BSD/macOS ("unterminated
  substitute") aunque funciona en GNU sed (Linux/CI). Se corrigió a
  `sed -i.bak "..." && rm -f *.bak`, portable entre ambos.

`promote`/`hotfix`/`cherry`/`check`/`versions`/`worktree *` no cambian.

---

## Consecuencias

### Positivas

- El manifiesto de cada proyecto refleja siempre la última versión release-quality
  preparada, visible con un simple `cat`/`grep`, sin consultar tags ni el registro.
- Elimina la clase de error "tagueé la versión equivocada porque escribí mal el argumento" —
  el argumento opcional de `tag` es solo confirmación, no la fuente del valor.
- Mismo contrato en `ai-tooling` y en los repos que lo consumen — no hay que reconciliar
  dos comportamientos distintos de `release.sh tag`.

### Negativas / Costos

- Cambia la firma de `release.sh tag` (rompe scripts/hábitos que pasaban `<version>` como
  segundo argumento obligatorio) — documentado en `CLAUDE.md`, `README.md` y el skill
  `gitops-monorepo`.
- Agrega un paso (`bump` + PR) antes de poder taguear.

---

## Alternativas consideradas

### Alternativa A: `release.sh tag` sigue recibiendo `<version>` y además bumpea+commitea+pushea el manifiesto a trunk automáticamente

Descartada: introduce un push directo a trunk dentro de un comando que además publica, y
mantiene dos fuentes de verdad conviviendo (el argumento y el manifiesto) hasta el momento
exacto del bump, en vez de una sola desde el inicio.

### Elegida: manifiesto como única fuente de verdad, separado en `bump` (PR revisado) + `tag` (lee y publica)

Ver también: `docs/adr/ADR-0007-gitops-monorepo-trunk-based.md`, y en `commons`:
`docs/adr/ADR-0010-manifiesto-fuente-de-verdad-version.md` (decisión original).
