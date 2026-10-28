# ADR-0072: Hardening del harness de sync — mitigación de riesgos

- **Estado:** Propuesto
- **Fecha:** 2026-10-28
- **Autor:** Juve + Kimi

---

## Contexto

El 2026-10-28, al integrar doclens como subsystem 3 de `auto-sync-daily.sh`, el
propio harness exhibió sus riesgos en vivo sobre school-system:

1. **Marker podrido sin detección:** `local_path: ~/ai-tooling` apuntaba a un
   directorio inexistente desde hacía meses. Nadie lo notó porque el fallback
   remoto funcionaba (lento) y el subsystem hooks quedaba en "sin sync" silencioso.
2. **`install-hooks.sh` es destructivo por default:** en una sola corrida pisó
   6 hooks y 3 scripts del consumer con versiones del toolbox, instaló 7 scripts
   nuevos y regeneró `settings.local.json` con un bloque `hooks` que **duplicaba
   13 hooks** ya registrados en `settings.json` del proyecto (CC mergea ambos
   archivos → doble ejecución). Se revirtió a mano con `git checkout`; el
   `settings.local.json` anterior no era recuperable (untracked).
3. **Footgun de jq:** `jq '.flag // true'` trata `false` como vacío — el opt-out
   `sync_hooks: false` era inerte hasta que se corrigió con `has()`. Un bug de
   una línea con blast radius completo.
4. **Hooks huérfanos invisibles:** school-system tenía 4 scripts de hook sin
   registrar y el propio `auto-sync-daily.sh` sin registrar — el harness llevaba
   meses sin correr en ese repo y nada lo señalaba.
5. **Opt-out = congelamiento:** con `sync_hooks: false`, los updates de hooks
   del toolbox (incluido el propio `auto-sync-daily.sh`) ya no llegan al
   consumer. Hoy es manual y depende de memoria.

**Decisión de fondo:** todo lo que sincroniza estado entre ai-tooling y los
consumers ES un solo harness — no se construyen mecanismos paralelos por
herramienta (un auto-check propio de doclens se hizo rollback ese mismo día por
esa razón). Por lo tanto el hardening se hace **dentro** del harness.

## Decisión

Cinco mitigaciones, en dos fases. Principio rector: **el harness reporta por
default, escribe solo con intención, y todo lo que escribe es reversible.**

### Fase 1 — Visibilidad (sin cambiar comportamiento de escritura)

**M1. `harness-doctor.sh` (nuevo, en `scripts/`)** — diagnóstico read-only.
**No existía:** `scripts/cc-health` es health-check HTTP del cc-proxy (otro
dominio); ninguna pieza del harness audita su propio estado hoy.

Decisión de deployment clave: el doctor **NO es distributable** —
`auto-sync-daily.sh` lo invoca directo desde `$AI_TOOLING_DIR/scripts/`
(igual que invoca `install-hooks.sh`), así el consumer siempre corre la
versión más nueva del diagnóstico y el doctor mismo no puede quedar stale.
Corre al final de `auto-sync-daily.sh` y loguea a `sync.log` (async, presupuesto
< 2s: sha256 de ~30 archivos es trivial); si el consumer no tiene `jq`, degrada
a solo los chequeos de archivos/versión.

- Marker: ¿`local_path` existe? ¿`ai_tool_version` del marker vs `VERSION` del
  toolbox (nuevo archivo, abajo)?
- Registry: hooks en `.claude/hooks/` vs registrados en `settings.json` +
  `settings.local.json` → reporta huérfanos y duplicados entre archivos.
- Drift: sha256 de hooks/scripts distributables del toolbox vs copias locales →
  reporta "N archivos con drift" **sin escribir nada**. Esto convierte el
  opt-out `sync_hooks: false` de "congelado e invisible" a "congelado pero
  monitoreado" — resuelve el riesgo 5.
  **Caveat (corrección de esta revisión):** sin el manifest de M3, el doctor no
  puede distinguir "drift por versión vieja" de "divergencia intencional del
  consumer" (ej. doc-guard.sh con ajustes locales). Por eso en fase 1 el drift
  es **solo informativo** (lista archivos, no juzga); la clasificación
  "modificado localmente vs desactualizado" llega con el manifest en M3.
- doclens: versión toolbox vs stamp del consumer.

**M2. `VERSION` en la raíz de ai-tooling** + chequeo de compatibilidad: si el
marker del consumer declara `ai_tool_version` con major distinto → warn fuerte.

### Fase 2 — Escritura segura (cambia install-hooks.sh)

**M3. `install-hooks.sh` con manifest + dry-run default:**

- Manifiesto `.claude/.harness-manifest.json`: sha256 de cada archivo que el
  harness instaló. Regla de escritura: solo sobrescribe si la copia local
  coincide con el manifest (el harness la puso y nadie la tocó) **o** con
  `--force`. Si el archivo local diverge del manifest → lo reporta como
  "modificado localmente" y lo salta. Esto elimina el riesgo de pisar
  customizaciones del consumer (riesgo 2).
- Default `--dry-run`: imprime el plan (qué copiaría, qué saltaría, qué
  registraría); escribe solo con `--apply`. `auto-sync-daily.sh` lo invoca con
  `--apply` únicamente cuando `sync_hooks: true`.
- **Dedupe de settings:** al regenerar `settings.local.json`, leer primero
  `settings.json` del proyecto y **no registrar hooks que ya estén ahí**.
  **Match por basename del script**, no por command literal (corrección de esta
  revisión): `settings.json` usa `bash .claude/hooks/x.sh` e install-hooks usa
  `"$CLAUDE_PROJECT_DIR"/.claude/hooks/x.sh` — el texto del command difiere
  aunque sea el mismo hook. Cero duplicados cross-file.
- **Backup antes de escribir:** tarball `.claude/.harness-backup-<ts>.tar.gz`
  con todo archivo que va a sobrescribir + el `settings.local.json` previo.
  Rollback = descomprimir.

**M4. Test del harness con fixture repo:** `tests/harness/` en ai-tooling con
un repo fake (marker + hooks dummy) y casos: opt-out false respeta (regresión
del bug jq), drift local no se pisa, settings.local.json sin duplicados,
sync_doclens no-op idempotente. Corre en pre-push del toolbox.

**M5. Convención de registro único por repo:** documentar en el README del
toolbox: un hook se registra en `settings.json` (proyecto, tracked) **o** vía
`install-hooks.sh` en `settings.local.json` (harness, untracked) — nunca en
ambos. `harness-doctor` (M1) enforcea reportando los duplicados.

## Consecuencias

- Positivas: el harness se vuelve observable (sync.log + doctor), reversible
  (backups), y seguro por default (dry-run + manifest). El opt-out deja de ser
  un punto ciego. El bug de jq queda cubierto por test de regresión.
- Negativas: `install-hooks.sh` crece (~150 líneas más) y hay que migrar una
  vez: primera corrida con manifest asume el estado actual como baseline.
- Compatibilidad: consumers sin manifest → primera corrida en modo
  "adopt": todo archivo local que difiera del toolbox se marca "local" y no se
  toca (conservador).

## Plan de adopción

1. Fase 1 (M1+M2): doctor + VERSION — una sesión. Cero riesgo, puro read-only.
2. Fase 2 (M3+M4): rewrite de install-hooks + tests — una sesión, validado
   contra school-system como consumer piloto.
3. M5: documentación + una pasada de dedupe en school-system (hoy: 13 hooks
   duplicados potenciales si settings.local.json vuelve a generarse).

## Referencias

- ADR-0043 (skills sync, soul.md), ADR-0047 (auto-sync-daily)
- Incidente origen: sesión 2026-10-28, commits `15e02874` (rollback del
  mecanismo paralelo) y `e0bd6c7` (integración doclens) en school-system;
  `b94dddf` (subsystem 3 + fix jq) en ai-tooling.
