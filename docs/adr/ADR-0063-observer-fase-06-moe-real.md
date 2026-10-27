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
2. **Cuantización mixta (ajustada en implementación):** los expertos de
   Qwen2MoE llegan FUSIONADOS en transformers 5.x (tensores 3D por capa, no
   Linear individuales) — ni bitsandbytes ni torchao los cuantizan (torchao
   int4 exige `mslk`, librería cerrada no pública). **Decisión final:**
   formato int4 propio group-wise (GROUP=64, escala fp16, nibbles en uint8,
   `hlwss/quant_int4.py`) con dequant perezoso solo de expertos seleccionados
   (~6.6GB), y el RESTO del modelo en bf16 (~3.6GB). Total ~10.5GB en RAM.
   El dequant perezoso es además la semilla conceptual de la política de
   residencia de Fase 1 (traer solo lo pedido).
3. **`dtype_bytes` por componente:** `BlockRegistry` acepta
   `expert_dtype_bytes` separado (expertos int4 ≈ 0.53 B/peso vs 2.0 bf16 del
   resto). Flags CLI: `--int4-experts`, `--dtype-bytes`, `--expert-dtype-bytes`.
   Nota: V1 (bytes esperados) usa un dtype único al re-derivar; para corridas
   MoE mixtas la comparación V1 de expertos queda deshabilitada — V2 (router)
   es el gate de este fase.
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
