# ADR-0065: Fase 0.7 — Simulator de políticas de residencia (replay de trazas)

**Estado:** aceptado · **Fecha:** 2026-10-06 · **Rama:** ex-observer-llm

## Contexto

Fase 0.6 midió el working set de expertos en Qwen1.5-MoE-A2.7B real: 6.7% de
los bytes de expertos se tocan por token, 39% de los bloques cubren 90% de los
accesos (reuso 8.6×). Ese es el insumo, no la conclusión: falta demostrar que
una política *model-aware* le gana al LRU del OS en **bytes transferidos**,
que es la métrica que la tesis promete mejorar. Construir el engine de Fase 1
para responderla es semanas; el PRD (v0.4 §2.2) ya designa al Simulator como
el paso intermedio: replay de eventos capturados bajo distintas políticas.

Decisión de diseño del dueño: en lugar de comparar políticas aisladas, se
diseña **una política unificada que fusione las fortalezas**, y las demás se
conservan solo como rieles de medición (piso y techo).

## Decisión

1. **Replay sobre el stream real de toques.** Entrada: `events.parquet` filtrado
   a `event_type == "LOGICAL_ACCESS"` con `block_id` no nulo, en orden
   `timestamp_ns`. Un toque = una demanda de residencia. `EXECUTE` no se
   replica (comparte tiempo base con LOGICAL_ACCESS; duplicaría toques).
2. **Unidad = bloque** del Registry (la más granular con ground truth medido;
   KV ya viene paginado como `L{i}.kv.p{j}`). Los tamaños se toman de
   `metadata.json → registry.blocks[].size_bytes` y de
   `kv_pages_template.kv_page_bytes` — **nunca** de `size_bytes` del evento,
   que mide bytes *tocados* (ej. una fila de embed), no el bloque. Bloques con
   `size_bytes == 0` (ties, p.ej. `lm_head` atado a `embed`) se excluyen: son
   residentes gratuitos. `block_id` desconocido → skip contado (`n_unknown`);
   no se infieren tamaños.
3. **Política candidata `hlwss` (fusión):**
   - *Pinning model-aware*: bloques con `component != "EXPERT"` (embed, attn,
     router implícito, shared_expert, KV plantilla base) + expertos top-frecuencia
     cubriendo 90% de los accesos EXPERT del propio trace (perfil aprendible
     de telemetría histórica — caveat honesto documentado).
   - *SLRU para el resto*: segmentos hot/cold; un touch en cold asciende el
     bloque a hot; eviction solo desde el LRU de cold (cold vacío → demote del
     LRU de hot). Captura sin lookahead la fortaleza práctica del oráculo:
     "mantener lo que se re-usa" (mismo principio que MySQL/Redis).
   - Pinned > budget → `infeasible=True` reportado como hallazgo, no crash.
4. **Rieles de medición**: `lru` (piso: cold start, evict least-recently-used)
   y `oracle` (techo: Belady, evict el residente cuyo próximo uso sea más
   lejano; next-use precomputado escaneando el stream hacia atrás). La
   pregunta que responde el experimento: ¿hlwss ≥ lru, y qué fracción del
   camino lru→oracle recorre?
5. **Métricas por corrida × política × budget**: bytes_transferred,
   ram_hit_fraction, bytes_por_token (vs `generated_tokens`), n_evictions,
   resident_peak, pinned_bytes, reduction_vs_lru. Budget en GB, sweepable.
6. **V1 NO emite `PHYSICAL_TRANSFER/PREFETCH/EVICT`** con `source="simulated"`
   aunque el enum exista para ello: es integración del engine Fase 1. V1
   responde la pregunta de la tesis con la tabla de métricas.

## Consecuencias

- Nuevo módulo `vendor/hlwss/hlwss/simulate.py` (~200 LOC) + subcomando
  `hlwss simulate` + tests con fixtures sintéticos (sin modelo ni red).
  Total proyectado ~2,090 < 2,500 (ADR-0064).
- El resultado sobre las 3 trazas MoE reales (`20e58cc0…`, `a581aa8c…`,
  `e820370d…`) se documenta en `ai-notes/findings/simulator-fase-07.md` y es
  el dato que go/no-go el engine de Fase 1.
- Caveat de fidelidad declarado: las frecuencias del pinning se miden del
  mismo trace replayado (no de una corrida independiente). Sesgo a favor de
  `hlwss` acotado y honesto; el rail `oracle` usa lookahead total, así que el
  espacio entre rails sigue siendo informativo.
- Fuera de scope v1: sweep de umbrales (hot-set %, tamaños de segmento) y
  trazas Ollama (sin `registry.blocks`; Fase 0.5 respondió su pregunta con
  vm_stat real).
