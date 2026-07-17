# ADR-0026 — Aceptar role "system" en el array messages

**Status:** Accepted  
**Date:** 2026-07-17  
**Author:** juvs

---

## Context

Claude Code (beta) puede enviar mensajes con `role: "system"` dentro del array
`messages` (no solo como campo top-level `system`). El modelo Pydantic `Message`
en `schemas.py` solo acepta `Literal["user", "assistant"]`, causando un 422
Unprocessable Entity antes de que el request llegue al pipeline de transformers.

Error observado: `messages[1].role = "system"` durante sesión de plan mode con Kimi K2.

## Decision

Ampliar el `Literal` de `Message.role` a `Literal["user", "assistant", "system"]`.

El código de conversión en `server.py` ya maneja `role == "system"` en mensajes
(línea 509). No se requieren cambios adicionales downstream.

## Consequences

- Requests con system turns inline dejan de ser rechazados con 422
- Sin impacto en la lógica existente — el Literal solo es validación de entrada
