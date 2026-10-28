# Plan — HLWSS Observer Fase 0.5: Ollama Spill Curve

> **Estado:** EN REVISIÓN (versión 2 — actualizado 2026-10-02: RAM confirmada 16GB por propietario; PRD §4 y §4.1/§4.2 actualizados en el mismo commit lógico)
> **Task ID:** hlwss-observer-fase-05-2026-10-02
> **Branch:** `ex-observer-llm` · **Código:** `vendor/hlwss/` · **ADR:** nuevo (ADR-0061, staging)
> **Fuente normativa:** PRD v0.4 §4 (Fase 0.5), §9 LOC (~150, límite 200), R6 (Ollama, no oMLX)
> **Depende de:** Fase 0 (completa, verificada V1–V4 el 2026-09-29)

---

## 1. Pregunta y objetivo

**Pregunta (PRD §4):** en este Mac, ¿dónde empieza el spill RAM→SSD y cuál es el bandwidth efectivo SSD→RAM durante decode? Número que ninguna fuente publica y que define si el Scheduler futuro (Fase 1 Simulator) tiene caso.

**Objetivo de la fase:** curva `tok/s vs (tamaño_modelo, contexto)` con atributos OS (page-ins, swap) + el caso documentado de la corrida real del propietario (9B @ 64k multi-turno, el "stall de Ollama" reportado). **Observación externa únicamente** — Ollama es caja negra (PRD §5: sin atribución semántica, aceptado).

## 2. Hallazgos del probe de entorno (2026-10-02)

| Dato | Valor | Impacto |
|---|---|---|
| Ollama | 0.34.3 (homebrew), server respondiendo en :11434 | `/api/generate` streaming + `/api/ps` disponibles |
| Modelos locales | `qwen2.5-coder:7b` (4.7GB), `frob/qwen3.5-instruct:9b` (6.6GB), `qwen3.5-hermes:latest` (6.6GB), `qwen-coder-hermes:latest` (4.7GB) | Escalera parcial: 7B→9B sin descargas |
| **RAM** | **16 GB** (`sysctl hw.memsize`) | ⚠️ PRD dice 12GB. El cruce FIT→SPILL se corre: 9B q4 + KV 64k ≈ 11GB sigue FIT; spill de KV requiere 9B @ ~128k o modelos ≥14B |
| Swap ahora | 0 usado, presión normal | Línea base limpia |
| Scripts repo | `bin/ollama-up`, `ollama-down`, `ollama-status` | Reutilizar para el harness |

**Consecuencia:** la escalera PRD (3B→9B→14B→32B) se adapta: cruzar a SPILL en 16GB exige 14B+ (~9GB descarga) o KV muy grande. **Decisión del owner requerida** (§10).

## 3. Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│  hlwss probe ollama --model X --ctx 65536 --turns 8         │
│                                                             │
│  ┌─────────────────┐   HTTP /api/generate (stream)         │
│  │  run_turn()     │────────► Ollama (caja negra)          │
│  │  · sampler hilo │◄──────── JSON final: eval_duration,   │
│  │    vm_stat+RSS  │         eval_count, load_duration...  │
│  │    cada 250ms   │                                       │
│  └────────┬────────┘                                       │
│           │ Eventos phase="request"                         │
│           ▼                                                 │
│  ┌─────────────────┐   ┌──────────────────┐                │
│  │ Observer (reuso)│──►│ writer.write()   │                │
│  └─────────────────┘   │ events.parquet   │                │
│                        │ metadata.json    │                │
│                        └──────────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

**Reuso máximo de Fase 0** (regla "wrap, don't rebuild"): `Event` (phase `request` ya existe en el enum), `Observer`, `RunConfig` (device="ollama"), `writer.write` (R7 request_id==run_id intacto), `cli` subcomando nuevo. **Cero cambios** a events/observer/writer/checks.

### Nuevo módulo: `hlwss/ollama_probe.py` (~140 LOC)

| Función/clase | Rol |
|---|---|
| `sample_os(proc_pid) -> dict` | snapshot único: `vm_stat` (pageins, swapins, swap usado) + `ps` RSS del proceso runner de Ollama. Puro, testeable |
| `class OsSampler` | hilo daemon: muestrea cada 250ms durante un turno, guarda (ts_ns, pageins, swapin, swap_used, rss). Overhead despreciable vs generación (ms por muestra) |
| `run_turn(model, prompt, num_ctx, keep_alive) -> (server_metrics, samples, text)` | POST `/api/generate` (`stream=true`, `options={num_ctx,temperature:0}`); consume el stream; del mensaje final extrae `eval_count/eval_duration/prompt_eval_count/prompt_eval_duration/load_duration/total_duration`; arranca/para el sampler; hace unload (`keep_alive:0`) si se pide |
| `to_events(run_id, turn_idx, server_m, samples) -> list[Event]` | server_metric → 1 evento agregado por turno (phase=`request`, component=`OTHER`, duration_ns desde eval_duration, size_bytes=eval_count); os_metric → N eventos de muestreo (duration_ns=0, size_bytes=RSS o pageins delta) |
| `cmd_spill(args)` (en cli.py, ~45 LOC) | driver multi-turno: crece el contexto (prompt fijo + historial acumulado), settle entre corridas, escribe trace + metadata enriquecida (model_size_bytes de /api/ps, num_ctx, turnos, memoria del host) |

**Convenios de schema:** `source ∈ {server_metric, os_metric}` (ya en el enum), `block_id=None`, `layer=None`, `token_pos=None`, `phase="request"`. Los campos `size_bytes`/`duration_ns` se reinterpretan según source (documentado en docstring + metadata: `metric_semantics`).

### CLI

```
hlwss probe ollama --model frob/qwen3.5-instruct:9b \
    --ctx 65536 --turns 8 --prompt-idx 0 \
    --out traces [--keep-alive 30m] [--unload-between]
hlwss report spill --traces traces   # tabla + bandwidth efectivo
```

## 4. Protocolo experimental

1. **Caso 0 (reproducción, primero):** 9B @ 64k, 8 turnos agenticos (prompts de `prompts/validation.json` encadenados: cada turno = prompt[i] + historial). `OLLAMA_KEEP_ALIVE=30m` para que el modelo NO se descargue entre turnos → KV acumula → **este es el escenario del stall reportado**. Sampler captura el colapso de tok/s si ocurre.
2. **Escalera FIT→SPILL** (matriz reducida según decisión §10):

| Modelo | Peso | 4k | 16k | 64k | 128k |
|---|---|---|---|---|---|
| qwen2.5-coder:7b | 4.7GB | FIT ref | FIT | FIT | FIT |
| frob/qwen3.5-instruct:9b | 6.6GB | FIT ref | FIT | **Caso 0** | FIT/spill-KV? |
| qwen3.5-hermes:latest | 6.6GB | FIT ref | FIT | FIT | FIT/spill-KV? |
| qwen3:14b *(descarga)* | ~9GB | FIT | FIT | spill-KV? | spill-weights? |

3. **Entre corridas:** `keep_alive:0` (unload) + 10s settle + snapshot vm_stat → línea base por corrida sin contaminación.
4. **Métricas por celda:** TTFT (`load_duration`+`prompt_eval_duration`), tok/s decode (`eval_count/eval_duration`), Δpageins, Δswapused, RSS pico del runner, presión de memoria (`memory_pressure` estado).

## 5. Derivados y reporte (`report spill`)

- **Curva spill:** tok/s vs contexto por modelo; inflexión donde tok/s colapsa y Δpageins se dispara = inicio del spill.
- **Bandwidth efectivo (régimen SPILL):** `bytes_no_residentes_por_token × tok/s` donde `bytes_no_residentes ≈ (tamaño_modelo + KV(ctx)) − RAM_disponible` acotado por observación de pageins reales. Se reporta con la fórmula explícita y el número medido de pageins (no asumido).
- **Clasificación por corrida:** FIT / SPILL-KV (pesos residentes, swap crece con ctx) / SPILL-WEIGHTS (pageins desde el inicio, load lento) — el eje del PRD §10.

## 6. LOC

| Módulo | LOC est. |
|---|---|
| `ollama_probe.py` | ~140 |
| `cmd_spill` + `cmd_report spill` en cli.py | ~45 |
| `tests/test_ollama_probe.py` (sampler puro con vm_stat mockeado, to_events, protocolo de settle) | ~60 (no cuenta al límite de fase) |
| **Fase 0.5 total** | **~185 (límite 200)** ✓ |

Total proyecto: ~1,029 (F0) + ~185 = ~1,214 < 2,000 (umbral revisión R5) ✓

## 7. Verificación (mandato de la fase)

1. `pytest tests/ -q` — 21+3 verdes (tests del probe no tocan red: vm_stat mockeado, to_events con datos sintéticos, semántica de Event)
2. Smoke: 7B @ 4k, 1 turno → tok/s del API ≈ tok/s pared ±10% (valida el muestreo de stream)
3. Caso 0 completo (9B @ 64k × 8 turnos) — si tok/s colapsa y pageins suben: **stall reproducido y documentado**
4. `report spill` genera la tabla completa sobre las trazas
5. `./scripts/task-verify.sh` al cierre

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| 16GB ≠ 12GB del PRD → cruce SPILL no alcanzado con modelos locales | Decisión owner (§10): descargar 14B (~9GB) o aceptar "spill no alcanzado" como resultado (también es dato: cuánto KV aguanta 16GB) |
| Corridas 64k/128k largas (prefill de minutos en 9B) | `--turns` y `--ctx` configurables; smoke primero; corridas grandes en background con monitor |
| `/api/generate` cambia campos entre versiones Ollama | Fijar 0.34.3 en metadata; fallar explícito si falta campo clave |
| Sampler de vm_stat cada 250ms distorsiona | Overhead ~ms; verificar con caso FIT (pageins planos con y sin sampler) |
| Descargas grandes sin autorización | Ninguna descarga automática; gate explícito del owner |

## 9. Backlog explícito (NO de esta fase)

- **Early-exit / "Jev-style":** el blog de TypeSafe (Jev) son claims de producto sin mecánica publicada — lo científico es medible YA con nuestro instrumento: probe de "¿la entropy de la capa k predice el argmax de la capa 28?" → política de corte con bytes ahorrados conocidos (~170MB/paso por capa 20-27) vs pérdida de calidad (harness PPL). Candidato Fase 1/2 junto al model loader agnóstico. Se registra en ADR-0061 como motivación, no se construye.
- **No** se toca: engine propio, políticas PREFETCH/EVICT (Simulator), oMLX verificación (30 min, PRD §5 — opcional si sobra tiempo).

## 10. Decisiones del owner (estado 2026-10-02)

1. **¿Autorizas descargar `qwen3:14b` (~9GB)** (y eventualmente 32B ~19GB) para forzar el cruce a SPILL en 16GB? *(alternativa gratis: escalera 7B→9B con ctx hasta 128k — SPILL-KV alcanzable sin descargas; SPILL-WEIGHTS no).* **← PENDIENTE**
2. ~~¿Confirmas 12GB→16GB?~~ ✅ **Confirmado por propietario 2026-10-02.** PRD §4 ajustado (escalera por rol de régimen).
3. **Turnos del Caso 0:** 8 por defecto, ¿ok? **← PENDIENTE**
4. **¿Implemento ya y dejo las corridas largas en background, o calendarizamos?** **← PENDIENTE**
