# ADR-0025 — EXIT_PLAN_SCAN_WINDOW configurable via env var

**Status:** Accepted  
**Date:** 2026-07-17  
**Author:** juvs

---

## Context

`_EXIT_PLAN_SCAN_WINDOW = 120` fue extraído como constante en ADR-0024.
El valor sigue siendo hardcodeado en el binario — no se puede ajustar por proveedor
sin cambiar código y hacer redeploy.

Algunos proveedores (Kimi K2 en particular) pueden necesitar ventanas diferentes
según su longitud de sesión típica o comportamiento en plan mode.

## Decision

Leer `_EXIT_PLAN_SCAN_WINDOW` del entorno con fallback al valor actual:

```python
_EXIT_PLAN_SCAN_WINDOW = int(os.getenv("EXIT_PLAN_SCAN_WINDOW", "120"))
```

Agregar `EXIT_PLAN_SCAN_WINDOW=120` a todos los `profile-envs/cloud.*.env`
para documentar el valor y permitir override por perfil.

## Consequences

- El valor default de 120 se preserva cuando la variable no está definida
- Cada perfil de proveedor puede ajustar la ventana sin cambio de código
- Sin impacto en tests: importan la constante Python, no el env var directamente
