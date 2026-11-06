# ADR-0082: `release.sh tag` y `promote` etiquetan el commit de `<remote>/<trunk>`, no HEAD

- **Estado:** Aceptado
- **Fecha:** 2026-11-06
- **Autor:** jeguzman

---

## Contexto

`release.sh tag` y `promote` etiquetaban el `HEAD` local. Un repo consumidor (ai_by_design, un monorepo con
pipeline único en Azure DevOps) evaluó los dos comandos en 32 escenarios con repos git reales. Salieron nueve
hallazgos:

1. `promote` no revisaba que el proyecto existiera. Creaba tags de proyectos inexistentes, que en un repo con
   force push denegado ya no se pueden borrar.
2. `promote dev` etiquetaba la rama actual o un commit sin subir. El pipeline después lo rechazaba, porque solo
   libera commits de trunk.
3. `promote` no comparaba la versión con el manifiesto: `promote svc 7.7.7 dev` funcionaba aunque `VERSION`
   dijera 1.2.3. En ese repo ya había pasado: se desplegó `0.0.9-dev.1` con `VERSION` en 0.0.8.
4. `tag` y `promote rc` exigían estar en trunk. En un repo donde todos trabajan en worktrees, trunk solo está en
   el checkout principal; un worktree no puede tenerlo. En un checkout de CI desprendido (`tag-multi-prod` de
   Bitbucket), `rev-parse --abbrev-ref HEAD` da `HEAD` y `tag` se negaba.
5. No leía `.gitops-env`: había que cargarlo a mano.
6. Un proyecto fuera de `GITOPS_PROJECT_MAP` se buscaba en `projects/`, aunque el repo tuviera sus proyectos en
   la raíz (es configuración: `GITOPS_PROJECTS_DIR=.`).
7. Un `VERSION` con CRLF se rechazaba.
8. Con trunk desfasado, sugería `release.sh sync` (rebase), que algunos repos prohíben.
9. El mensaje final decía «el pipeline de Bitbucket» también en Azure DevOps.

Seis de los nueve (1 a 4, 8 y el árbol sucio) tienen la misma causa: lo que se etiqueta depende del checkout.

## Decisión

**El commit de un release es el de `<remote>/<trunk>`.** `tag` y `promote` hacen fetch de trunk y etiquetan ese
commit con `git tag -a <tag> <sha>`, sin checkout. Corren desde cualquier rama, worktree o checkout desprendido,
con el árbol sucio, y solo liberan lo que ya entró a trunk.

- **Rollback:** `promote <svc> <dev|rc> --at <ref>` etiqueta otro commit, que debe ser ancestro de
  `<remote>/<trunk>`.
- **Proyecto y versión:** se leen de ese commit. El proyecto debe existir ahí, y la versión sale de su manifiesto
  (`pyproject.toml`, `package.json` o `VERSION`), sin `\r` ni espacios, y debe ser X.Y.Z.
- **Firma de `promote`:** `promote <svc> <dev|rc>` toma la versión del manifiesto. La firma anterior,
  `promote <svc> <X.Y.Z> <dev|rc>`, sigue valiendo solo si X.Y.Z es la versión del manifiesto.
- **N de `-dev.N` y `-rc.N`:** sale de los tags del remote (`ls-remote`). Si otro sube el mismo N entre la cuenta
  y el push, se recalcula hasta 3 veces.
- **`.gitops-env`** de la raíz se lee solo. Se toman solo líneas `GITOPS_*=valor`, como datos (nunca se ejecuta),
  y lo que ya trae el entorno gana.
- **Mensajes:** sin `sync`; el de éxito, genérico.

Sin cambio: `tag` sigue anotado, sube solo su tag, rechaza uno que ya existe (también si solo existe en el remote)
y se bloquea con un hotfix sin cherry-pick. `hotfix`, `cherry`, `check`, `bump` y `versions` no cambian.

## Consecuencias

### Positivas

- Un release no depende del estado del checkout: el mismo comando da el mismo tag desde la Mac, un worktree o el
  CI.
- No se puede etiquetar algo que no pasó por el PR de trunk, ni con una versión distinta a la de su manifiesto.
- `tag-multi-prod` de Bitbucket funciona en el checkout desprendido del CI.

### Negativas / Costos

- `tag` y `promote` ya no avisan de un árbol sucio o de trunk desfasado en local: lo ignoran, porque no lo usan.
- Un rollback ya no es «checkout del commit viejo y promote»; es `--at <ref>`.
- `promote` con una versión distinta a la del manifiesto ahora falla. Quien lo usaba para re-etiquetar una
  versión vieja debe pasar `--at` al commit de esa versión.

## Implementación

- `scripts/release.sh`: `_load_gitops_env`, `_release_commit`, `_require_project_at`, `_manifest_version_at`,
  `_push_tag`, `cmd_tag`, `cmd_promote` y la ayuda.
- Tests: `tests/gitops/release-tests.sh`, 27 escenarios con repos git reales (H1 a H9, uno por hallazgo; R1 a R12,
  lo que no debe romperse, incluidos el checkout de CI desprendido y un repo con trunk `main` sin
  `.gitops-env`). Con la versión anterior fallan 24.
