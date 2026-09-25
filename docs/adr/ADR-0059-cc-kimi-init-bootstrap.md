# ADR-0059: cc-kimi-init — bootstrap por-proyecto de Claude Code con Kimi for Coding

- **Date**: 2026-09-24
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

Usar Claude Code (CLI y extensión de VS Code) contra Kimi for Coding requiere cuatro piezas por proyecto, descubiertas de forma empírica en ai-tooling (2026-09-23):

1. `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` alcanzables por el CLI (bloque `env` en `.claude/settings.local.json`).
2. `apiKeyHelper` en settings del proyecto: el gate de autenticación del modo **interactivo** (extensión de VS Code) no consulta el `env` de los settings — solo variables de entorno reales del proceso, `apiKeyHelper`, OAuth token o login guardado. Sin helper, la extensión responde `Not logged in · Please run /login` aunque el `env` esté correcto.
3. Entrada en `.gitignore` del proyecto para `.claude/settings.local.json` (contiene la key).
4. `hasTrustDialogAccepted: true` en `~/.claude.json` bajo la ruta del proyecto — sin trust, el CLI ignora el `env` y las reglas de permisos del settings del proyecto.

Replicar estas cuatro piezas a mano en cada proyecto nuevo es error-prone (dos de los cuatro fallos reales fueron exactamente esto: template del proxy copiado sin editar, y trust no aceptado).

## Decision

1. **Centralizar la key fuera de los proyectos.** La API key vive una sola vez en `~/.config/cc-kimi/api-key` (permisos 0600), junto a `key-helper.sh`, un script genérico que imprime el contenido de ese archivo. Rotar la key = editar un archivo.

2. **Template de settings idéntico para todos los proyectos.** `templates/claude/settings.local.json.kimi` en ai-tooling contiene el bloque `env` (base URL Kimi, context tokens) y `apiKeyHelper` apuntando al helper **central** — no contiene la key ni rutas del proyecto, por lo que se copia tal cual.

3. **Tool `tools/cc_kimi_init.py`.** Python tipado, stdlib-only, salida estricta en JSON por stdout (logs a stderr). CLI contract:
   `python3 tools/cc_kimi_init.py [TARGET_DIR] [--key-file PATH] [--force] [--no-trust] [--dry-run]`
   - Crea/actualiza `~/.config/cc-kimi/` (si `--key-file` se da, siembra la key).
   - Copia el template a `<TARGET_DIR>/.claude/settings.local.json` (no sobrescribe si existe, salvo `--force`).
   - Agrega `.claude/settings.local.json` y `.claude/kimi-key-helper.sh` al `.gitignore` del proyecto si faltan (idempotente).
   - Marca `hasTrustDialogAccepted` en `~/.claude.json` para la ruta resuelta del target (salvo `--no-trust`).
   - Rutas de config sobreescribibles vía `CC_KIMI_CONFIG_DIR` y `CC_CLAUDE_JSON` para tests.

4. **Tests obligatorios** en `tools/tests/test_cc_kimi_init.py`: happy path en tmpdir, idempotencia de gitignore, preservación de `settings.local.json` existente sin `--force`, trust escrito, salida JSON válida.

## Consequences

**Positive:**
- Setup de proyecto nuevo = un comando; los cuatro fallos conocidos quedan imposibles por construcción.
- La key deja de replicarse en N helpers por proyecto: una sola fuente de verdad en `~/.config/cc-kimi/`.
- El template commiteado en ai-tooling no contiene secretos ni rutas personales — reusable por cualquiera del equipo con su propia key central.

**Negative / Trade-offs:**
- Dependencia implícita en el layout de `~/.claude.json` (estructura `projects.<path>.hasTrustDialogAccepted`) — si Anthropic cambia el formato, el trust silenciosamente deja de aplicar (el CLI mostrará el diálogo de nuevo, fallo visible y recuperable).
- El helper central rompe el aislamiento perfecto por proyecto: cualquier proyecto con `apiKeyHelper` apuntando a `~/.config/cc-kimi/key-helper.sh` usa la misma key. Aceptado — es el trade-off deliberado de centralizar.
- Base URL hardcodeada al endpoint Anthropic-compatible de Kimi (`https://api.kimi.com/coding`); cambiar de proveedor requiere editar el template o el `settings.local.json` del proyecto.
