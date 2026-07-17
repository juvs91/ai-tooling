# ADR-0024 — Extraer _EXIT_PLAN_SCAN_WINDOW como constante exportada

**Status:** Accepted  
**Date:** 2026-07-17  
**Author:** juvs

---

## Context

`_exit_plan_already_called()` en `deferred_tools.py` usa `window=120` como parámetro default.
Los tests de boundary en `test_deferred_tools.py` tenían el número `120` (y `121`) hardcodeados.
Cuando el valor cambió de 60 a 120 en una sesión anterior, los tests quedaron stale y fallaron.

## Decision

Extraer `_EXIT_PLAN_SCAN_WINDOW = 120` como constante de módulo exportada.
Los tests importan la constante y calculan los límites (`window + 1` para "outside").

## Consequences

- Cambiar el window en un solo lugar actualiza automáticamente los tests de boundary
- Sin cambio de comportamiento en runtime
