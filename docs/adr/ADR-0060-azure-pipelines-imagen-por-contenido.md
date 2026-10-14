# ADR-0060: Templates de Azure Pipelines — imagen por contenido y promoción por digest

- **Estado:** Propuesto
- **Fecha:** 2026-10-14
- **Autor:** juvs91
- **Relacionados:** ADR-0007 (GitOps monorepo), ADR-0034 (manifest = versión), ADR-0044 (worktrees)

---

## Contexto

Los templates de `templates/gitops/` solo cubren Bitbucket Pipelines y publican a GCP.
Un monorepo nuevo en Azure DevOps (`ai_by_design`, Metalsa) necesita el mismo modelo de
ADR-0007 sobre Azure Pipelines, publicando imágenes a **Azure Container Registry** y
librerías Python a **Azure Artifacts**.

El diseño obvio ("build en merge a `main` → imagen `sha-<commit>` → re-tag en cada tag
git") **rompe el modelo** por cómo funciona `release.sh`:

1. `release.sh tag` crea el tag de prod en el **HEAD de trunk**, no en el SHA del `-rc`
   (`cmd_tag`). Si `main` avanzó con commits de otros servicios, el pipeline de este
   servicio — filtrado por paths — nunca construyó una imagen para ese HEAD: el promote
   falla, o un rebuild manda a prod una imagen que QA no probó.
2. `-hotfix.N` vive en `hotfix/*`, no en `main` → nunca se construyó imagen.
3. `release.sh promote … dev` se permite desde cualquier rama.
4. Dos builds del mismo commit no son reproducibles bit a bit: digests distintos.

Además, en Microsoft Learn **no está documentado** si los filtros `paths` se evalúan en
pushes de tags, ni cómo se comportan los patrones de tag con `@`, ni si el check
*Branch control* acepta `refs/tags/*` (detalle en la bitácora de ai_by_design).

## Decisión

1. **La imagen se identifica por contenido.** `ci-image.sh context-hash` calcula
   `ctx-<16hex>` con los tree hashes git del servicio + sus libs compartidas. Los commits
   de otros servicios no cambian el hash.
2. **Eventos de tag idempotentes.** Si `ctx-<hash>` ya existe en ACR solo se re-taggea;
   si no, se construye. Cubre hotfix, `promote dev` desde ramas y builds saltados.
3. **Prod promueve el digest del último `X.Y.Z-rc.N`** (`ci-image.sh prod-source`), no
   `ctx-*`. Antes exige `ci-image.sh same-content <rc> <prod> <rutas>`: si el contenido
   del servicio cambió desde QA, el pipeline falla. El invariante de ADR-0007 pasa de
   "mismo SHA" (que `release.sh` nunca garantizó) a **"mismo contenido"**.
4. **Inmutabilidad en ACR.** Tras publicar, el tag y el manifest (`repo@digest`) se
   bloquean por separado (`--write-enabled false --delete-enabled false`; Learn: los
   locks de tag y manifest son independientes). Alcanza con AcrPush. `acr purge` salta
   los bloqueados.
5. **Re-tag sin rebuild** con `docker buildx imagetools create` (AcrPush), no
   `az acr import` (intra-registry no documentado, rol *Data Importer*).
6. **Dos pipelines por servicio:** `<servicio>-ci.yml` (rama `main` + `paths`) y
   `<servicio>-release.yml` (solo `tags`, sin `paths`). No dependemos de la interacción
   no documentada entre `tags` y `paths`.
7. **Una sola lista de dependencias por servicio**, declarada como parámetro del
   template y repetida en `trigger.paths.include` (los triggers no aceptan parámetros);
   `ci-image.sh check-paths` falla si divergen.
8. **Checkout explícito:** `fetchDepth: 0`, `fetchTags: true` (defaults de pipelines
   nuevos: depth 1 y sin tags). SHA del tag siempre con `git rev-parse "$TAG^{commit}"`.
9. **Prod con defensa en profundidad:** Environment con approval + `condition` sobre
   `Build.SourceBranch` + guard en script (tag sin sufijo). El check *Branch control*
   con `refs/tags/*` se configura pero no se asume.
10. **Libs Python → Azure Artifacts:** versiones inmutables, no hay re-tag. Cada ambiente
    publica su versión PEP 440 desde el mismo SHA (`X.Y.Z.devN`, `X.Y.ZrcN`, `X.Y.Z`) en
    `@Local` y se promueve a la view `@Prerelease`/`@Release` por REST.
11. **Auth sin secretos:** service connections con workload identity federation;
    `TwineAuthenticate@1` para el feed.
12. Los templates de Bitbucket **no se tocan**; los de Azure viven en
    `templates/gitops/azure-pipelines/`.

## Consecuencias

- **Positivas:** prod recibe exactamente el digest que QA aprobó; hotfix y dev desde ramas
  funcionan sin casos especiales; la lógica de decisión es testeable sin nube
  (`tools/tests/test_ci_image.py`).
- **Negativas:** dos YAML por servicio; la lista de dependencias aparece dos veces en el
  YAML (mitigado con `check-paths`); el primer `-rc` de un contenido nuevo puede
  requerir build si `main` aún no lo construyó.
- **Pendiente de validar en un pipeline sandbox:** paths con tags, varios tags en un push,
  patrones con `@`, Branch control con `refs/tags/*`, `imagetools create` contra ACR.
