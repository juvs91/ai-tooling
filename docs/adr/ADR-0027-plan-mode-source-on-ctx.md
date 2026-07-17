# ADR-0027 — Exponer plan_mode_source en TransformContext

**Status:** Accepted  
**Date:** 2026-07-17  
**Author:** juvs

---

## Context

Cuando CC activa plan mode vía toggle (Signal 1, `_pm_source="cc"`), CC misma llama
EnterPlanMode del lado cliente. El modelo solo necesita llamar ExitPlanMode al final.

`_PLAN_MODE_EXIT_NOTE` en `plan_mode_enforcement.py` actualmente le pide al modelo que
llame EnterPlanMode como PASO 1, lo cual es incorrecto cuando Signal 1 está activo.
Kimi K2 lo ignoró correctamente en la prueba 2026-07-17, pero la nota confusa generó
un nombre de plan file incorrecto.

Cuando Signal 2 está activo (`_pm_source="proxy"` — proxy detectó intent PLAN sin CC
toggle), el modelo sí debe llamar EnterPlanMode + ExitPlanMode.

## Decision

1. Agregar `plan_mode_source: str = "cc"` a `TransformContext` en `pipeline.py`.
2. El `IntentClassifierTransformer` setea `ctx.plan_mode_source = _pm_source`.
3. `PlanModeEnforcementTransformer` usa dos notas distintas según `ctx.plan_mode_source`:
   - `"cc"` → solo ExitPlanMode (CC ya gestionó EnterPlanMode)
   - `"proxy"` → Enter + ExitPlanMode (el modelo debe hacer ambos)

## Consequences

- Elimina la instrucción incorrecta de EnterPlanMode cuando CC toggle está ON
- Cualquier transformer futuro puede leer `ctx.plan_mode_source` sin acceder a session cache
- Sin impacto en el clasificador — solo expone un dato ya computado
