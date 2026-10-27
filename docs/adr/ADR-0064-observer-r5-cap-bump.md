# ADR-0064: R5 cap de LOC 2,000 → 2,500 (hlwss)

**Estado:** aceptado · **Fecha:** 2026-10-06 · **Rama:** ex-observer-llm

## Contexto

R5 fijó un techo de 2,000 LOC para `vendor/hlwss/hlwss/` con el criterio de
auditabilidad: pocas líneas = cada una visible. Al cierre de Fase 0.6 el
total medido era 1,866. El 2026-10-06 se ejecutó una dieta de LOC sobre los
archivos editables (cli.py, ollama_probe.py, early_exit.py) con resultado
**honesto y negativo**: 1,871 (+5). Extraer helpers en Python cuesta ~7 líneas
fijas (def + docstring + blank) y el boilerplate duplicado era de ~3 líneas
por sitio; la grasa estimada (150-250 LOC) no existe — el código de
verificación (registry/checks/adapter) ya es denso y cada línea carga
semántica de medición.

## Decisión

1. **El cap R5 sube de 2,000 a 2,500 LOC**, con el criterio ampliado por el
   dueño (2026-10-06): "LOC puede subir siempre y cuando estemos completamente
   seguros de que lo que medimos es verídico y fidedigno". El criterio de
   admisión por feature pasa de tamaño a **fidelidad demostrable** (checks
   V1–V4 verdes + tests del feature).
2. **La dieta queda como práctica, no como gate:** deduplicar sigue siendo
   deseable (la sesión consolidó 5 rutas de metadata-walking en `_metas()`),
   pero ya no se exige reducción neta de LOC antes de un feature nuevo.
3. **El margen consumible es 2500 − 1871 = 629 LOC**, asignado en orden:
   Simulator Fase 0.7 (~220), engine Fase 1 con PREFETCH/EVICT (~300+), y el
   resto a mantenimiento. Si Fase 1 supera el margen, el siguiente bump
   exigirá antes un repaso de fidelidad (qué tan verídicas son las V1–V4).

## Consecuencias

- El Simulator (ADR-0065) puede implementarse sin recortar código de
  verificación — que es justo el que garantiza que lo simulado es verídico.
- Riesgo aceptado: 2,500 sigue siendo pequeño en términos absolutos
  (~1 archivo típico de un proyecto grande); la auditabilidad se conserva
  porque el crecimiento queda presupuestado por fase, no abierto.
- El conteo exacto se reporta en cada sesión de cierre (`wc -l hlwss/*.py`)
  y cualquier feature que lo incremente lo declara en su checklist.
