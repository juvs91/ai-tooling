# Análisis: fallos de hooks en sesión 92941296-7d4f-4160-9ba4-5381f7ae5e3b

**Sesión:** `claude --resume 92941296-7d4f-4160-9ba4-5381f7ae5e3b`
**Transcript:** `/Users/jeguzman/.claude/projects/-Users-jeguzman-ai-tooling/92941296-7d4f-4160-9ba4-5381f7ae5e3b.jsonl`
**Duración:** 2026-08-12T05:27:52Z → 05:33:55Z (~6 min, 190 líneas de transcript)
**Terminada por:** el usuario rechazó un tool call (`ls docs/adr/`) a los 05:33:44Z — abandono manual de la sesión.

## Qué pidió el usuario originalmente

Diagnosticar un warning del proxy:
```
WARNING:server:[session] Hourly cache cleanup failed: ProxyMetrics.record() takes 2 positional arguments but 3 were given
```
Nada relacionado con hooks o skills — ese fue un efecto colateral del propio proceso de investigación.

## Root cause confirmado

A las **05:30:59.862Z**, el agente corrió:
```
cd /Users/jeguzman/ai-tooling/vendor/claude-code-proxy && grep -rn "class ProxyMetrics\|def record\b" llm proxy router utils *.py
```
La tool Bash de Claude Code **persiste el cwd entre comandos dentro de la misma sesión** (comportamiento documentado). Ese `cd &&` cambió el cwd persistente del shell de `/Users/jeguzman/ai-tooling` a `vendor/claude-code-proxy` **para el resto de la sesión** — no fue un cambio aislado al comando.

Desde ese instante, cada PreToolUse/PostToolUse que invoca un hook del proyecto vía **ruta relativa** (`bash .claude/hooks/<script>.sh`, definido así en `.claude/settings.json`) empezó a fallar con **exit 127** porque `.claude/hooks/` ya no existía relativo al nuevo cwd:

```
Failed with non-blocking status code: bash: .claude/hooks/track-skill-load.sh: No such file or directory
Failed with non-blocking status code: bash: .claude/hooks/edit-drift-detector.sh: No such file or directory
Failed with non-blocking status code: bash: .claude/hooks/block-dangerous.sh: No such file or directory
Failed with non-blocking status code: bash: .claude/hooks/protect-skill-gate-bypass.sh: No such file or directory
```

**Los 4 archivos existen y están intactos en disco** (`ls .claude/hooks/` confirmado) — nunca hubo un skill ni un script "no encontrado" en sentido literal. Lo que el usuario vio scrolleando en la terminal fue el mensaje `track-skill-load.sh: No such file or directory` repetido — de ahí la percepción de "skills not found" (el nombre del script contiene "skill-load").

Los hooks que SÍ siguieron funcionando durante ese tramo usan **rutas absolutas vía variable de entorno**:
```
python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse.py    → 100% hook_success
python3 ${CLAUDE_PLUGIN_ROOT}/hooks/posttooluse.py   → 100% hook_success
npx block-no-verify@1.3.0                             → resuelto por npm, no depende del cwd
```
Esto confirma que el problema es específicamente el patrón de ruta relativa en `.claude/settings.json`, no una corrupción del entorno ni de los scripts.

## Conteo exacto de eventos (todo el transcript)

| Tipo | Cantidad |
|---|---|
| `hook_success` | 49 |
| `hook_non_blocking_error` | 34 |
| `hook_cancelled` | 9 |
| otros (`skill_listing`, `deferred_tools_delta`, etc.) | 8 |

Los 34 `hook_non_blocking_error` se concentran **exclusivamente** entre 05:31:24Z y 05:33:29Z (~2 min), es decir, desde el `cd` en adelante — cero fallos antes de ese punto. Se reparten en 4 scripts:
- `track-skill-load.sh` — 10 fallos
- `block-dangerous.sh` — 9 fallos
- `edit-drift-detector.sh` — 8 fallos
- `protect-skill-gate-bypass.sh` — 7 fallos

## Hallazgo de seguridad (no solo cosmético)

`block-dangerous.sh` y `protect-skill-gate-bypass.sh` son hooks de seguridad (bloquean comandos destructivos y bypasses de skill-gate). Al fallar como `hook_non_blocking_error` (exit 127, no exit 2), el harness los trata como **fail-open**: durante esos ~2 minutos, cualquier comando que debía bloquear `block-dangerous.sh` habría pasado sin control. En esta sesión no se ejecutó ningún comando destructivo real, así que no hubo impacto, pero el patrón (hook de seguridad + ruta relativa + fail-open) es una debilidad estructural, no solo ruido de logs.

## Nada relacionado con el "Skill tool"

Se verificó explícitamente: no hubo ningún error real de carga de skill (`Skill` tool, `ToolSearch`, lectura de `SKILL.md`) en la sesión. El único `Skill(workflow-coordinator)` de la sesión se ejecutó correctamente al inicio. Los "skills not found" reportados son 100% los 10 fallos de `track-skill-load.sh` por el problema de cwd.

## Fix recomendado

En `.claude/settings.json`, los comandos de hooks del proyecto usan rutas relativas al cwd (`bash .claude/hooks/X.sh`). Deben resolverse contra la raíz del repo, no contra el cwd del shell persistente, por ejemplo:
```
bash "$CLAUDE_PROJECT_DIR/.claude/hooks/X.sh"
```
(si Claude Code expone `CLAUDE_PROJECT_DIR` o variable equivalente — verificar en la doc de hooks vigente) o resolviendo `git rev-parse --show-toplevel` dentro de cada script antes de cualquier lookup relativo. Esto es una **decisión de configuración de hooks**, no un cambio a `vendor/claude-code-proxy/` ni a `.agents/skills/`, así que no dispara el ADR-First Gate.

## No revisado

- No se investigó el bug original del proxy (`ProxyMetrics.record()`) — quedó interrumpido por el fin abrupto de la sesión, sin relación con este hallazgo.
- No se corrigió `.claude/settings.json` en este análisis (modo `analysis`, solo lectura/reporte).
