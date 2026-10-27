# ADR-0061: Fase 0.5 — Ollama Spill Curve (observación externa)

**Estado:** Aceptado (2026-10-02) · **Fase:** 0.5 · **Plan:** `.claude/plans/observer-llm-fase-05.md` · **PRD:** v0.4 §4

## Contexto

El PRD v0.4 §4 (decisión R6 del propietario) fija Fase 0.5 = curva de spill de Ollama: en qué punto RAM→SSD ocurre el cruce FIT→SPILL y cuál es el bandwidth efectivo de spill durante decode — el parámetro que el Simulator de Fase 1 necesita y que ninguna fuente publica. El contexto de campo asumía Mac de 12 GB; el 2026-10-02 `sysctl hw.memsize` midió **16 GB**, lo que corre el cruce (ver §Corrección de hardware).

## Decisiones

1. **Observación 100% externa, caja negra.** Ollama no se instrumenta ni parchea (PRD §5: sin atribución semántica, aceptado). Dos planos de medición:
   - `server_metric`: API oficial `/api/generate` (stream) — `eval_count/eval_duration` (tok/s decode), `prompt_eval_*` (prefill), `load_duration`. Sin scraping de logs ni APIs privadas.
   - `os_metric`: `vm_stat` (pageins/swapins/swapouts en bytes) + RSS del proceso runner (`ps`), muestreados por hilo cada 250 ms durante cada turno. Swap usado vía `sysctl vm.swapusage` (vm_stat no lo reporta).
2. **Reuso del pipeline Fase 0.** Los eventos usan el schema v0.4 existente: `phase="request"` (ya previsto en PRD §7), `source` ∈ {server_metric, os_metric}, `block_id="host.<métrica>"` para el plano OS. `Observer`, `Event` y `writer.write` sin cambios.
3. **Semántica de campos en eventos request-level** (documentada en `metadata.metric_semantics` por corrida): `size_bytes` = tokens generados (server) o valor de la métrica en bytes (os); `duration_ns` = `eval_duration` (server).
4. **Stress de KV por contexto, no solo por turnos.** Los turnos agenticos cortos (~256 tok/turno) no llenan 64k de KV; el driver acepta `--filler-file` repetido por turno para hacer crecer el contexto real hasta `num_ctx`. Sin esto, "9B @ 64k" mediría FIT disfrazado.
5. **Límite LOC Fase 0.5 ≤ 200** (R5): un módulo nuevo `ollama_probe.py` + dos subcomandos CLI. Sin dependencias nuevas (urllib stdlib).
6. **Corrección de hardware registrada:** 16 GB (no 12 GB). Escalera ajustada en PRD §4: cruce SPILL-KV alcanzable sin descargas vía ctx ≥128k en 9B; SPILL-WEIGHTS requiere 32B (~19 GB, gate explícito del propietario — no autorizado al cierre de esta ADR, no se descarga nada).
7. **Caso 0 (reproducción del stall del propietario):** 9B @ 64k, 8 turnos, `keep_alive=30m` para acumulación real de KV. Con 16 GB la hipótesis inicial cambia de "spill de pesos" a FIT/SPILL-KV leve; el sampler de pageins lo distinguirá.

## Consecuencias

- El Simulator de Fase 1 recibirá: curva tok/s vs (modelo, ctx), Δpageins/Δswap por corrida, clasificación FIT/SPILL-KV/SPILL-WEIGHTS y bandwidth efectivo **medido** (bytes paginados por token × tok/s) en vez de asumido.
- Limitación aceptada (PRD §5): agregado por corrida/turno, sin atribución por capa/bloque — eso sigue siendo territorio del engine propio (Fase 1).
- `vm_stat`/`ps`/`sysctl` son macOS; el reporte de memoria Linux (`/proc/meminfo`) queda como port pendiente de Fase 1 (Mac↔Linux).
