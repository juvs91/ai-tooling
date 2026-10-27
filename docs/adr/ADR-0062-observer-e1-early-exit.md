# ADR-0062: E1-A — Early-exit probes (logit-lens por capa)

**Estado:** aceptado · **Fecha:** 2026-10-02 · **Rama:** ex-observer-llm

## Contexto

Fase 0/0.5 estableció la línea base: el decode naive toca todos los pesos por
token (~2.4GB/token agregados en Qwen3-0.6B) y, en régimen SPILL, eso colapsa a
0.15 tok/s. La tesis del proyecto es que una política *model-aware* de residencia
puede ganarle al LRU del OS. Una palanca de demanda directa es el early exit
(prior art: CALM 2020, logit-lens): si el argmax se estabiliza antes de la última
capa, las capas posteriores son bytes que no eran necesarios en la ruta crítica.

## Decisión

1. **E1-A mide, no corta.** Se registran forward-hooks post-capa que aplican
   `lm_head(norm(h_l))` en cada capa intermedia durante la generación greedy, y
   se compara el argmax por capa contra el argmax final. Sin tocar el loop de
   inferencia del Adapter (el corte real es E1-B, decisión separada).
2. **Solo se guardan estadísticos** (argmax, entropía, top1_prob por capa/token),
   nunca logits completos: 28 capas × N tokens × 3 floats ≪ logits de vocab 151k.
   El schema de eventos del observer no cambia (R-schema intacto); el reporte
   vive como sidecar `early_exit.json` en el directorio de la traza.
3. **El umbral τ se reporta, no se fija:** `first_layer_at(τ)` para
   τ ∈ {0.90, 0.95, 0.99} + acuerdo por capa. Elegir τ = decisión de E1-B con
   datos de PPL en mano, no a priori.
4. **Composición con el Adapter:** el probe coexiste con la instrumentación
   HLWSS estándar en la misma corrida (`--early-exit-probe`): una sola pasada
   produce traza de bytes (Fase 0) y estabilidad de argmax (E1-A), lo que habilita
   V1-E (bytes esperados dinámicos según capa de salida) sin re-correr.

## Consecuencias

- Nuevo módulo `vendor/hlwss/hlwss/early_exit.py` (~120 LOC), flag en `hlwss run`,
  tests sobre el MoE sintético (CPU, deterministic).
- El reporte estima bytes ahorrables (pesos por capa × capas saltadas) — el puente
  cuantitativo entre early exit y la tesis de spill.
- E1-B (corte real + PPL wikitext2 + V1-E) queda como ADR posterior, gated por
  los resultados de E1-A.
- Riesgo aceptado: el overhead del probe (norm+lm_head por capa por token) sesga
  levemente el wall-clock; no afecta los eventos HLWSS (duration_ns mide el par
  pre/post del bloque, no el total de pasada).
