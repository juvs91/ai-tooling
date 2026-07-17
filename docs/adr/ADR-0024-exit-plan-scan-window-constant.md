# ADR-0024 — EXIT_PLAN_SCAN_WINDOW: constante exportada + variable de entorno

**Status:** Accepted  
**Date:** 2026-07-17  
**Author:** juvs

---

## Context

`_exit_plan_already_called()` en `deferred_tools.py` usa `window=120` como parámetro default.
Los tests de boundary en `test_deferred_tools.py` tenían el número `120` (y `121`) hardcodeados.
Cuando el valor cambió de 60 a 120 en una sesión anterior, los tests quedaron stale y fallaron.
Además, el valor no era ajustable por entorno sin cambiar código.

## Decision

1. Extraer `_EXIT_PLAN_SCAN_WINDOW` como constante de módulo exportada, leída del entorno:
   ```python
   _EXIT_PLAN_SCAN_WINDOW = int(os.getenv("EXIT_PLAN_SCAN_WINDOW", "120"))
   ```
2. Los tests importan la constante y calculan los límites relativos (`WINDOW - 1`, `WINDOW + 1`).
3. Agregar `EXIT_PLAN_SCAN_WINDOW=120` a todos los `profile-envs/cloud.*.env` para documentar
   el valor por defecto y permitir override por proveedor.

## Consequences

- Cambiar el window en un env file no requiere cambios de código
- Los tests de boundary siempre reflejan el valor actual de la constante
- El valor default de 120 se mantiene si la variable no está definida
