# Tool Index

Catálogo central de scripts en `tools/`. Antes de escribir un tool nuevo,
consulta este índice y `AGENTS.md` — reusar/extender un tool existente es
preferible a crear uno nuevo (Deduplication Mandate, ver
`.agents/skills/core/tool-writer/SKILL.md`).

---

## `tools/check_skill_frontmatter.py`

**Qué hace:** Recorre recursivamente `.agents/skills/**/SKILL.md` (o
cualquier `--root`/`--filename` parametrizado) y valida que cada archivo
tenga un bloque de frontmatter YAML (`---` ... `---`) con un campo `name`
(o `--field` parametrizado) no vacío.

**Cómo usarlo:**
```bash
python tools/check_skill_frontmatter.py                              # default: .agents/skills, SKILL.md, campo name
python tools/check_skill_frontmatter.py --root .agents/skills --field name
python tools/check_skill_frontmatter.py --filename AGENT.md --field id  # reusable para otros contratos
python tools/check_skill_frontmatter.py --json                       # salida JSON estricta para consumo agéntico
```
Exit codes: `0` todo válido · `1` al menos un archivo problemático · `2`
error de uso (`--root` inexistente).

**Cuándo usarlo:**
- Antes de commitear un `SKILL.md` nuevo o editado, para confirmar que el
  `Skill` tool podrá resolverlo (necesita `name` no vacío).
- Como gate de CI/pre-commit sobre `.agents/skills/`, análogo a lo que
  `check_adr_gate.py` hace para ADRs.
- Como chequeo puntual tras un `sync_skills.sh` para detectar SKILL.md
  rotos antes de que fallen en tiempo de uso.

**Constraints:**
- Solo stdlib (sin PyYAML) — el parseo de frontmatter es deliberadamente
  ingenuo: detecta delimitadores `---` y una clave de nivel superior
  (`campo:` sin indentación). No valida el resto del YAML ni soporta
  comentarios inline después del valor.
- Un archivo con "cero coincidencias" bajo `--root` se reporta como éxito
  (0 revisados, 0 problemas), no como error — el caller debe decidir si
  ese conteo es en sí mismo sospechoso para su caso de uso.
- No corrige nada — solo reporta. Los archivos problemáticos detectados
  deben corregirse manualmente (y, si están bajo `.agents/skills/`, esa
  edición cae bajo el ADR-First Gate del repo).

**Caso de uso concreto:** Corrida contra el árbol real de este repo
(2026-08-03): 76 archivos `SKILL.md` revisados, 3 problemáticos —
`.agents/skills/archaeology/squit/SKILL.md`,
`.agents/skills/core/orchestrator/SKILL.md` y
`.agents/skills/frontend/frontend-analysis/SKILL.md`, los tres sin ningún
bloque de frontmatter (empiezan directo con un heading `#`). Ver
`docs/adr/ADR-0045-check-skill-frontmatter-tool.md` para el detalle de
diseño y este hallazgo.

**Tests:** `tools/tests/test_check_skill_frontmatter.py` (17 tests,
`python3 -m unittest tools/tests/test_check_skill_frontmatter.py -v`).

---

## `tools/check_adr_gate.py`

**Qué hace:** Enforcer del ADR-First Gate. Verifica que cualquier cambio a
rutas guardadas (`vendor/claude-code-proxy/**/*.py`, `.agents/skills/**/*.md`,
o lo que defina `.claude/adr-gate.conf`) venga acompañado de un nuevo ADR en
`docs/adr/` (o de `--skip-adr` / `[skip-adr]` en el commit message).

**Cómo usarlo:**
```bash
python tools/check_adr_gate.py \
    --changed-files "vendor/claude-code-proxy/server.py" \
    --new-files     "docs/adr/ADR-0046-nueva-decision.md" \
    --commit-message "feat: add transformer"
```
Sin `--changed-files` explícito, cae a `git diff --cached` (uso típico:
pre-commit hook, `.claude/hooks/adr-gate.sh`).
Exit codes: `0` gate abierto · `1` gate fallido.

**Cuándo usarlo:** Automáticamente vía pre-commit hook (`tools/install_hooks.sh`)
y vía `.claude/hooks/adr-gate.sh` (PreToolUse de Claude Code). Rara vez se
invoca manualmente.

**Constraints:** Requiere que los ADRs nuevos sigan numeración secuencial
estricta (`ADR-NNNN` = último existente + 1). No cubre rutas fuera de
`GUARDED_PATTERNS`/`.claude/adr-gate.conf` — notablemente, `tools/` NO está
guardado.

**Caso de uso concreto:** Bloquea un commit que edita
`vendor/claude-code-proxy/llm/pipeline.py` sin un ADR nuevo en staging,
salvo que el commit message incluya `[skip-adr]`.

---

## `tools/check_adr_sections.py`

**Qué hace:** Recorre (sin recursión) `docs/adr/ADR-*.md` (o cualquier
`--root`/`--pattern` parametrizado) y valida que cada ADR tenga las
secciones obligatorias `Status, Date, Context, Decision` (o
`--sections` custom). Detecta cada sección en dos formas equivalentes —
heading (`## Context`, `## Decision Outcome`) o campo inline en negrita
(`- **Date**: 2026-03-22`, `**Status:** Accepted`) — porque el corpus real
de ADRs de este repo mezcla ambos estilos.

**Cómo usarlo:**
```bash
python tools/check_adr_sections.py                              # default: docs/adr, Status,Date,Context,Decision
python tools/check_adr_sections.py --sections Status,Date,Context,Decision
python tools/check_adr_sections.py --i18n-es                    # acepta también Estado/Fecha/Contexto/Decisión
python tools/check_adr_sections.py --json                       # salida JSON estricta para consumo agéntico
```
Exit codes: `0` todos completos · `1` al menos un ADR con sección(es)
faltante(s) · `2` error de uso (`--root` inexistente).

**Cuándo usarlo:**
- Antes de commitear un ADR nuevo o editado, para confirmar que tiene las
  4 secciones mínimas que el resto del sistema (ADR-first gate, agentes
  que leen ADRs como fuente de verdad) asume que existen.
- Como chequeo puntual/CI sobre `docs/adr/`, análogo a lo que
  `check_skill_frontmatter.py` hace para `.agents/skills/`.

**Constraints:**
- Sin `--i18n-es`, un ADR redactado enteramente en español (`Estado`,
  `Fecha`, `Contexto`, `Decisión`) se reporta como si le faltaran las 4
  secciones en inglés — es el comportamiento correcto por default (el
  usuario pidió verificar esos nombres literales), no un bug; usar
  `--i18n-es` para un chequeo más laxo sobre el mismo corpus mixto.
- No corrige nada — solo reporta. No valida el contenido de cada sección,
  solo su presencia.
- Heading con texto adicional (ej. `## Context and Problem Statement`,
  `## Decision Outcome`) cuenta como sección presente vía prefix-match con
  límite de palabra — no exige que el heading sea exactamente el nombre.

**Caso de uso concreto:** Corrida contra `docs/adr/` real (2026-08-03): 45
ADRs revisados. En modo estricto (default), 7 incompletos — 6 de ellos
(`ADR-0007`, `ADR-0017`, `ADR-0018`, `ADR-0034`, `ADR-0035`, `ADR-0044`)
son ADRs redactados enteramente en español y resuelven limpio con
`--i18n-es`. El séptimo, `ADR-0022-workflow-coordinator-task-scope-generation.md`,
tiene un gap real incluso con `--i18n-es`: usa `## Status\nAccepted — 2026-07-15`
(fecha embebida como texto libre dentro de `Status`, no como campo `Date`/`Fecha`
propio) — le falta `Date` en cualquier modo. Ver
`docs/adr/ADR-0046-check-adr-sections-tool.md` para el detalle de diseño.

**Tests:** `tools/tests/test_check_adr_sections.py` (20 tests,
`python3 -m unittest tools/tests/test_check_adr_sections.py -v`).

---

## `tools/install_hooks.sh`

**Qué hace:** Distribuye hooks/scripts marcados con `# distributable: true`
desde `ai-tooling` a otro proyecto, e instala el pre-commit hook local que
invoca `check_adr_gate.py`.

**Cómo usarlo:** Ver comentarios del propio script — se corre tras
actualizar hooks en `ai-tooling` para propagarlos a repos hijos.

**Cuándo usarlo:** Al onboardear un proyecto nuevo al sistema de ADR-gate/hooks,
o tras modificar un hook marcado `distributable: true` en `ai-tooling`.

**Constraints:** Bash — asume entorno POSIX-like (no probado en Windows sin
WSL/Git Bash).

---

## `tools/cc_kimi_init.py`

**Qué hace:** Bootstrap de un comando para correr Claude Code (CLI y
extensión de VS Code) contra Kimi for Coding. Aplica las cuatro piezas
necesarias por proyecto (ver ADR-0059): copia el template
`templates/claude/settings.local.json.kimi` a `.claude/settings.local.json`,
crea el key store central `~/.config/cc-kimi/` (key 0600 + helper genérico,
seeded con `--key-file`), agrega entradas al `.gitignore` del proyecto y
marca workspace trust en `~/.claude.json`.

**Cómo usarlo:**
```
python3 tools/cc_kimi_init.py [TARGET_DIR] [--key-file PATH] [--force] [--no-trust] [--dry-run]
```
Salida estricta en JSON por stdout (logs a stderr). Exit 0 = ok, 2 = error
de uso/validación. Overrides de rutas vía `CC_KIMI_CONFIG_DIR` y
`CC_CLAUDE_JSON`.

**Cuándo usarlo:** Al iniciar Claude Code por primera vez en un proyecto
nuevo contra Kimi, o para migrar un proyecto que hoy tiene la key en un
helper por-proyecto al key store central.

**Constraints:** La key NO se copia al proyecto — el `apiKeyHelper` apunta a
`~/.config/cc-kimi/key-helper.sh`; si ese directoro no existe o está vacío,
hay que pasar `--key-file` la primera vez. El trust asume el layout actual
de `~/.claude.json` (`projects.<path>.hasTrustDialogAccepted`). Requiere el
template en `templates/claude/settings.local.json.kimi` (rutas relativas al
repo). Base URL hardcodeada a `https://api.kimi.com/coding`.

**Use case:** `python3 tools/cc_kimi_init.py /Volumes/case-sensitive/squit --key-file ~/.config/cc-kimi/api-key` deja squit listo para abrir VS Code y hablarle a Claude sin login ni prompts.

**Tests:** `tools/tests/test_cc_kimi_init.py` (7 tests,
`python3 -m unittest tools/tests/test_cc_kimi_init.py -v`).
