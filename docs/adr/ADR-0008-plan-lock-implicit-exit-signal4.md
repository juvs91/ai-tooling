# ADR-0008: P0 PLAN_LOCK — Signal 4 Implicit Exit vía CC UI Mode Change

**Status**: Accepted  
**Date**: 2026-06-25  
**Context**: Proxy running Kimi K2 (primary) + DeepSeek (classifier), `ai-tooling-proxy_cloud-1`

## Problem

Cuando el usuario activa el modo Plan de Claude Code (`/plan`) y el modelo llama `EnterPlanMode`,
el proxy activa `plan_mode_active = True` vía tres señales:

| Señal | Fuente | Limpieza automática |
|-------|--------|---------------------|
| Signal 0 | `EnterPlanMode` en historial de mensajes | Solo cuando `ExitPlanMode` aparece en historial |
| Signal 1 | `"Plan mode is active"` en system prompt (inyectado por CC) | Sí — CC lo elimina cuando el usuario cambia a Autoedit/Bypass |
| Signal 3 | Session cache persistente (`compressor.py`) | Solo cuando `ExitPlanMode` es llamado |

**El gap**: Cuando el usuario cambia de `/plan` → Autoedit/Bypass en la UI de CC:
1. CC **deja de inyectar** `"Plan mode is active"` → Signal 1 se limpia ✓
2. Pero `EnterPlanMode` sigue en el historial sin `ExitPlanMode` → Signal 0 activo ✗
3. La session cache sigue diciendo `plan_mode_active=True` → Signal 3 activo ✗
4. **P0 PLAN_LOCK se dispara** → el proxy fuerza `intent=PLAN` ignorando la UI

Este problema es especialmente severo con Kimi K2 porque:
- Kimi K2 no siempre llama `ExitPlanMode` al terminar el plan (a diferencia de Claude)
- El DeepSeek classifier (post ADR-0006) inyecta reglas explícitas de PLAN enforcement
- El usuario queda atrapado en plan mode indefinidamente sin workaround en-sesión

## Decision

Añadir **Signal 4 — Implicit ExitPlanMode** en `intent_classifier.py` (líneas ~502-508).

**Condición de activación** (AND lógico de tres condiciones):
1. `plan_mode_active == True` (Signal 0 o Signal 3 activos)
2. `"Plan mode is active" not in _system_text` — CC ya no está en modo Plan (Signal 1 ausente)
3. `ctx.intent in ("BUILD", "VERIFY")` — el classifier detectó intento explícito de implementar

**Efecto**: Limpia `plan_mode_active = False` antes de que P0 PLAN_LOCK evalúe, permitiendo
que el intent BUILD/VERIFY fluya normalmente hacia enforcement de EXECUTE.

### Justificación del diseño

- **Signal 1 como proxy de CC UI state**: Es el único canal in-band disponible. CC inyecta
  `"Plan mode is active"` exactamente mientras el usuario está en `/plan` mode. Su ausencia
  es evidencia confiable de que CC salió del modo Plan.

- **Guarda BUILD/VERIFY**: Previene falsos positivos durante sesiones de planificación activa.
  Mientras el modelo está leyendo/planificando, el classifier retorna READ, PLAN, o CHAT —
  no BUILD. Signal 4 solo se activa cuando el usuario pide explícitamente implementación.

- **No afecta `EnterPlanMode` manual**: Si el modelo llama `EnterPlanMode` fuera de CC /plan
  (e.g., via workflow-coordinator), Signal 1 está ausente por default — pero la misma guarda
  BUILD/VERIFY aplica: durante planificación activa el intent no es BUILD.

### Caso no cubierto (known limitation)

Si el usuario invoca `EnterPlanMode` manualmente (no via CC /plan), escribe el plan, y luego
dice "implementa ahora" (intent=BUILD) sin CC /plan activo, Signal 4 limpia el lock.
Esto es el comportamiento deseado: el usuario explícitamente quiere implementar.

## Consequences

- Kimi K2 (y cualquier modelo) puede salir del PLAN_LOCK cambiando la UI de CC a Autoedit
  y enviando un mensaje de implementación — sin necesidad de llamar `ExitPlanMode`
- El workaround anterior ("dile al modelo que llame ExitPlanMode") ya no es necesario
- Costo mínimo: una comparación de string por request cuando `plan_mode_active=True`
- Tests: nueva clase `TestSignal4ImplicitExitPlanMode` en `test_intent_classifier.py`
