# ADR-0063: Fase 0.6 — Observación de MoE real (Qwen1.5-MoE-A2.7B)

**Estado:** aceptado · **Fecha:** 2026-10-02 · **Rama:** ex-observer-llm

## Contexto

La palanca de mayor ganancia potencial del mapa estratégico es la residencia
aware-MoE: en un MoE real cada token toca solo ~19% de los pesos (2.7B activos
de 14.3B en Qwen1.5-MoE-A2.7B). Ese porcentaje —el working set real por token—
es EL insumo que el Simulator de Fase 1 necesita para simular una política
expert-aware vs el LRU del OS. Hoy solo se midió en un MoE sintético (V2).
El dueño autorizó la descarga (2026-10-02).

## Decisión

1. **Modelo:** Qwen/Qwen1.5-MoE-A2.7B — el MoE público real más pequeño con
   estructura de expertos genuina (60 expertos, top-4, 2.7B activos).
2. **Cuantización 4-bit vía torchao (weight-only int4)** para caber en 16GB
   (14.3B bf16 = 28GB no cabe; int4 ≈ 8GB sí). La cuantización es *habilitador
   de observación*, NO objeto de estudio: se registra en metadata y listo.
3. **`dtype_bytes` explícito (0.5)** en el registry: `model.dtype` reporta el
   dtype de las escalas (bf16) bajo torchao, no los bytes reales por peso.
   Los touch_size deben reflejar bytes reales en RAM (int4), no el dtype de
   cómputo. Se expone como flag `--dtype-bytes` (default: comportamiento previo).
4. **Sin cambios al schema de eventos ni al engine.** V2 ya captura selección
   de expertos; el adapter ya maneja expertos fusionados (transformers 5.x).
   El análisis nuevo es agregación: frecuencia por experto, working set único
   por token, concentración (cuántos expertos cubren el 90% de accesos).
5. **Prompts:** batch corto y diverso (generación abierta + factual), mismo
   patrón que E1-A.

## Consecuencias

- Descarga de ~28GB (bf16) — se cuantiza en load; descartar el cache fp tras
  validar si el disco aprieta.
- Nuevo comando de reporte: `hlwss report moe` (frecuencia por experto,
  working set por token, concentración).
- Si torchao int4 no funciona en MPS, fallback: CPU con int8 dynamic quant
  (lento pero la observación no mide velocidad) — decisión menor documentada
  en el PRD/plan, no requiere ADR nuevo.
- Los eventos de expertos cuantizados usan dtype_bytes=0.5: las fórmulas V1
  comparan contra cfg del modelo con el mismo parámetro (consistente).
