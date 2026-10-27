# HLWSS — Observer PRD v0.4 (Dev-ready, validado por propietario)

> Consolida v0.3 + revisión humana (`HLWSS-PRD-v0.3-review.md`) con las decisiones del propietario del 2026-09-24.
> Hardware del propietario: **Apple Silicon, 16 GB RAM** ⬅️ *corregido 2026-10-02 (confirmado por propietario vía `sysctl hw.memsize`); versiones ≤ 2026-09-24 decían 12 GB — las referencias históricas quedan anotadas en su lugar*.
> Estado: **APROBADO PARA DESARROLLO** — Fase 0 (PyTorch) + Fase 0.5 (Ollama spill curve).

***

## 0. Resolución de la revisión v0.3

| Hallazgo                                 | Resolución                                                                                                                                                              |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1 — fórmula V1 inflada por `embed`      | **Aplicado** (§2.3): eventos llevan bytes realmente tocados; Registry deduplica bloques tied; normas fuera del Registry                                                 |
| R2 — KV derivado asume atención completa | **Aplicado** (§2.4): la fórmula KV se deriva del `config.json` del modelo (detecta sliding window, dtype de KV)                                                         |
| R3 — oMLX metrics ✅ sin verificar        | **Revertido a 🟡** + tarea de verificación antes de cualquier trabajo Mode A                                                                                            |
| R4 — MoE de V2 en 12 GB                  | **Decidido:** V2 se valida con **MoE sintético** (arquitectura real, pesos aleatorios — §3). MoE real se difiere a Fase 1 (hardware o cuantización)                     |
| R5 — límite 1,500 LOC                    | **Decisión por defecto (reversible):** límite **por fase** — Fase 0 ≤ 800, Fase 0.5 ≤ 200; revisión si el proyecto total cruza 2,000                                    |
| R6 — Mode A                              | **Decidido por propietario:** Fase 0.5 = **Ollama** (no oMLX), con escalera de modelos Qwen. Motivo original (12 GB): Ollama+mmap es el escenario SPILL real en producción hoy. *Revisado 2026-10-02:* con **16 GB** el cruce FIT→SPILL requiere modelo ≥14B o KV ≥128k — escalera ajustada en §4 |
| R7 — menores                             | Todos aplicados (§7)                                                                                                                                                    |

**Contexto de campo del propietario (2026-09-24, no verificado independientemente):** corrió `frob/qwen3.5-instruct:9b` con ventana de 64k vía Ollama/Hermes en el Mac (entonces reportado como 12 GB; **medido 16 GB el 2026-10-02**). Que esa corrida haya funcionado implica KV cuantizado; con 16 GB, 9B q4 (6.6 GB) + KV 64k ≈ 11 GB total → **régimen más probable: FIT, no spill** — la hipótesis de "spill parcial" de la versión original baja de probabilidad. **Reproducirla bajo observación sigue siendo el primer experimento de Fase 0.5**, ahora para *confirmar el régimen* (FIT vs SPILL-KV) con datos en vez de inferirlo. El stall que reportó el propietario ("después de varios turnos se muere") migró de hipótesis "spill de pesos" a candidatos: (a) crecimiento de KV multi-turno cruzando a SPILL-KV, (b) fragmentación/presión de memoria del host, (c) bug de Ollama/Hermes — los tres distinguibles con el sampler de pageins/swap de Fase 0.5.

***

## 1. Fuentes

Sin cambios en las 10 originales (verificadas el 2026-09-21, tabla completa en v0.2 §1). Ollama pasa de "baseline futuro" a **objetivo de observación de Fase 0.5**; oMLX queda como referencia (su tiered KV hot/cold SSD es el diseño modelo-aware a batir) pero fuera del MVP.

**Dos fuentes agregadas (verificadas el 2026-09-24):**

| #  | Fuente                                                                                             | Qué aporta a HLWSS                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| -- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 11 | **PagedAttention / vLLM** — paper SOSP 2023 + doc de diseño (docs.vllm.ai/design/paged\_attention) | Es **la referencia canónica de aplicar memoria virtual del OS a estado de LLM**: KV cache paginado en bloques de tamaño fijo con tabla de bloques lógico→físico. HLWSS extiende la misma idea un nivel abajo en la jerarquía (RAM↔SSD). Grounding directo para la distinción logical/physical y para la granularidad de KV (ver §2.2). ⚠️ Nota: la página de documentación consultada es **histórica** (no describe el código actual de vLLM); la cita durable es el paper SOSP'23 (Kwon et al.).                                                                                                          |
| 12 | **TensorRT-LLM** — github.com/NVIDIA/TensorRT-LLM                                                  | No es backend objetivo (NVIDIA-only; el propietario está en Apple Silicon), pero aporta tres validaciones de dirección: (a) su blog "Evaluating Agentic Serving with **Trace Replay** and Job-Level Metrics" (2026-08-20) valida en producción la metodología observe→replay del What-if Simulator; (b) sus *weight-stripped engines* (+99% compresión, pesos separados del engine) son prior art de gestión de pesos desacoplada del runtime; (c) su roadmap (KV cache reuse, sparse attention, MoE expert parallelism) confirma que la gestión de KV y expertos es el campo de batalla — refuerza H1/H3. |
| 13 | **System One / "Jev"** — typesafe.ai/blog/introducing-system-one-models-and-jev (2026, verificado 2026-10-02) | Modelo de decisión tipada **no-autoregresivo** con confianza calibrada (RLCD). Aporta validación *económica* (el costo del loop autoregresivo que Fase 0 midió es real y el mercado ya construye alternativas) y motiva el experimento E1 (§4.1/§4.2). Sin mecánica publicada — **no es evidencia técnica**, no cambia el diseño. |

***

## 2. Especificación del Observer (canónica)

### 2.1 Schema de eventos v0.4

```python
@dataclass
class Event:
    timestamp_ns: int        # time.monotonic_ns() — único reloj del Observer
    request_id: str          # uuid4 por corrida; debe ser igual a metadata.run_id
    token_pos: int | None    # posición absoluta en decode; None en prefill
    phase: str               # "prefill" | "decode" | "request"  (request = nivel agregado, Fase 0.5)
    layer: int | None
    component: str           # ATTN | FFN | EXPERT | KV | EMBED | LM_HEAD | OTHER
    block_id: str | None     # §2.2
    event_type: str          # EXECUTE | LOGICAL_ACCESS | PHYSICAL_TRANSFER | PREFETCH | EVICT | SKIP
    size_bytes: int | None   # bytes REALMENTE tocados por este evento (§2.3) — no el tamaño del bloque
    duration_ns: int | None  # CPU wall del hook; en MPS/CUDA NO es tiempo de device
    source: str              # "hook" | "profiler" | "server_metric" | "os_metric" | "derived" | "simulated"
```

### 2.2 Block Registry

`block_id` = unidad de residencia/transferencia de todo el sistema. Convención:

| Modelo | block\_id                                              |
| ------ | ------------------------------------------------------ |
| Dense  | `embed`, `L{i}.attn`, `L{i}.ffn`, `lm_head`, `L{i}.kv` |
| MoE    | + `L{i}.expert.{j}`, `L{i}.shared_expert` (si existe)  |

Sidecar JSON: `block_id → {layer, component, size_bytes, tied_with: str|null}`.

* `size_bytes` del Registry = tamaño total del bloque tal como está almacenado (lo usa el Simulator).

* Bloques *tied* (p. ej. `embed` == `lm_head`) se registran **una sola vez**; el otro apunta vía `tied_with`.

* **Normas (RMSNorm), rotary y biases quedan fuera del Registry** (<1% de bytes, absorbidos por la tolerancia de V1).

* **El KV se registra paginado (refinamiento desde PagedAttention, fuente #11):** `L{i}.kv` no es un bloque monolítico sino una secuencia de páginas `L{i}.kv.p{j}` de `KV_PAGE_TOKENS` tokens (default 16, como vLLM; configurable por corrida y registrado en metadata). Cada página: `2 × n_kv_heads × head_dim × dtype_bytes × KV_PAGE_TOKENS` bytes. Esto alinea la unidad de residencia de KV con la que usan los sistemas de producción y permite al Simulator razonar sobre eviction parcial de contexto en vez de "todo el KV de la capa".

### 2.3 Reglas de emisión

1. Todo módulo hookeado emite **1 EXECUTE + 1 LOGICAL\_ACCESS por bloque poseído**, mismo timestamp base.
2. **`size_bytes` del evento = bytes realmente tocados**, no el tamaño del bloque:

   * `attn`, `ffn`, `expert`, `lm_head`: tamaño completo del bloque (matmul lee todo).

   * `embed`: **una fila** (`hidden_size × dtype_bytes`) en prefill y decode — nunca el bloque completo.
3. Router MoE: 1 LOGICAL\_ACCESS por experto seleccionado + log auxiliar con logits crudos para V2.
4. Eventos KV: sintéticos (`source="derived"`), §2.4.
5. PHYSICAL\_TRANSFER / PREFETCH / EVICT: no se emiten en PyTorch; en Ollama solo a nivel agregado (§4); los emite el Simulator por política.
6. SKIP: solo en experimentos de FFN skipping (post-Fase 0).

### 2.4 KV derivado desde config.json

El adapter lee el `config.json` del modelo cargado y construye la fórmula por capa:

* Atención completa: `2 × n_kv_heads × head_dim × dtype_bytes × context_len`

* Sliding window (si `sliding_window=W`): `context_len → min(context_len, W)`

* Registra en metadata: `kv_model: "full" | "swa:{W}" | "quant:{type}"`.

### 2.5 Metadata de corrida (canónica)

```json
{
  "schema_version": "0.4",
  "run_id": "uuid4", "timestamp_utc": "...",
  "model_id": "...", "quant": "...", "n_layers": 0, "n_experts": 0,
  "backend": "pytorch|ollama_api|mlx|omlx_api", "backend_version": "...",
  "hardware": "...", "os": "...",
  "ram_total_gb": 12, "ram_available_gb": 0, "swap_used_at_start_gb": 0,
  "ssd_model_path": "...",
  "seed": 0, "temperature": 0.0, "do_sample": false,
  "prompt_hash": "sha256", "max_tokens": 0, "context_window": 0,
  "kv_model": "full", "quality_harness": "wikitext2-ppl-slice-v1",
  "clock": "time.monotonic_ns"
}
```

### 2.6 Presupuesto de overhead

≤ 5% en tokens/segundo con instrumentación completa vs run idéntico sin Observer (temperature=0, seed fija). Si se supera: muestreo determinístico 1-de-N por `token_pos`.

***

## 3. Fase 0 — Observer PyTorch (validación de fidelidad)

**Config canónica:** batch=1, single-stream, decode-focused, temperature=0, seed fija, 5 prompts fijos (`prompts/validation.json`), `max_tokens=256`.

* **V1 (dense) — fidelidad de bytes.** Modelo: **Qwen3-0.6B fp16** (\~1.2 GB — cómodo en 12 GB).
  `expected = Σ bytes de bloques no-KV (con dedup de tied, embed como fila) + KV derivado por paso (§2.4)`
  `measured = Σ size_bytes de LOGICAL_ACCESS por token_pos`
  Criterio: `|measured − expected| / expected ≤ 10%` promediado.
  *Nota de implementación: leer hidden/vocab/tie\_word\_embeddings/sliding\_window del config.json real — no hardcodear.*

* **V2 (routing MoE) — CON MODELO SINTÉTICO (decisión R4).** Como ningún MoE real cabe en 12 GB en PyTorch, V2 instancia una **arquitectura MoE real escalada** (p. ej. config tipo OLMoE con 4 capas, 8 expertos, hidden pequeño, **pesos aleatorios**). V2 valida que el hook captura decisiones del router (`hook == topk(router_logits)` al 100%) — eso depende de la arquitectura, no de los pesos. Un MoE real (OLMoE-1B-7B \~14 GB bf16, o cuantizado) queda para Fase 1.

* **V3 (overhead):** tokens/seg con Observer ≥ 95% del baseline.

* **V4 (reproducibilidad):** misma seed+prompt → secuencia idéntica de `(event_type, block_id, layer, component, phase, token_pos)`. Si MPS introduce no-determinismo: `torch.use_deterministic_algorithms(True)`.

**Salida de Fase 0:** V1/V3/V4 ✅ en Qwen3-0.6B, V2 ✅ en MoE sintético, Analyzer reproduce bytes/token + working set + reuso confirmados por cálculo teórico, trazas legibles en pandas/Polars/DuckDB.

***

## 4. Fase 0.5 — Ollama Spill Curve (decisión R6 del propietario)

**Pregunta:** en un Mac de 12 GB, ¿dónde empieza el spill RAM→SSD y cuál es el bandwidth efectivo SSD→RAM durante decode? Es el número que ninguna fuente publica y el que define si el scheduler futuro tiene caso.

**Método — escalera de modelos × contextos:**

* Modelos (GGUF q4 vía Ollama), **escalera ajustada a 16 GB (2026-10-02)** — cada peldaño tiene rol de régimen explícito:

| Peldaño | Peso q4 | Rol en 16 GB | Estado |
|---|---|---|---|
| `qwen2.5-coder:7b` | 4.7 GB | FIT de referencia (línea base de tok/s sin presión) | ✅ instalado |
| `frob/qwen3.5-instruct:9b` | 6.6 GB | FIT → SPILL-KV por contexto (barato: subir `num_ctx`, sin descarga) | ✅ instalado |
| `qwen3.5-hermes:latest` | 6.6 GB | Segunda muestra FIT→SPILL-KV (arquitectura hermes, KV distinta) | ✅ instalado |
| Qwen **14B** | ≈ 8–9 GB | **Frontera FIT**: cabe holgado; SPILL-KV ya en ctx moderado | ⚠️ descarga ~9 GB (gate propietario) |
| Qwen **32B** | ≈ 18–20 GB | **SPILL-WEIGHTS garantizado** (pesos > RAM usable) | ⚠️ descarga ~19 GB (gate propietario) |

  *Versión histórica (12 GB):* "3B → 9B → 14B → 32B, el cruce ocurrirá en algún punto". En 12 GB el 9B @ 64k ya rozaba spill; **en 16 GB el cruce FIT→SPILL-KV se logra más barato por contexto (9B @ ≥128k) que por modelo**, y el SPILL-WEIGHTS exige 32B. Decisión de descargas = del propietario (§4 arriba, plan `.claude/plans/observer-llm-fase-05.md` §10).

* Contextos: 4k / 16k / 64k / **128k** (el cuarto peldaño se agregó con 16 GB: es el que fuerza SPILL-KV en los modelos instalados sin descargar nada; el KV crece hasta competir con los pesos).

* Mediciones por corrida (todo sin root):

  * API Ollama (`source="server_metric"`): `load_duration`, `prompt_eval_duration/count`, `eval_duration/count` → TTFT, tok/s. Eventos `phase="request"` agregados.

  * OS (`source="os_metric"`): `vm_stat` (page-ins, swap usage) antes/después de cada corrida.

* Derivado: en régimen SPILL, `bandwidth_efectivo ≈ bytes_del_modelo_no_residentes_por_token × tok/s`. Se reporta por modelo y contexto, con la curva completa tok/s vs tamaño.

* Reproducir la corrida real del propietario (9B @ 64k, multi-turno con KEEP_ALIVE largo) como caso documentado: ¿estaba en FIT, SPILL-KV o SPILL-WEIGHTS? *(Con 16 GB la apuesta inicial cambió: FIT o SPILL-KV leve; el sampler de pageins lo dirá.)*

### 4.1 Lecciones externas (2026-10-02) — System One / "Jev" (TypeSafe)

Fuente 13 (§1). Post de producto, **sin mecánica publicada** — se registra lo que transfiere y lo que no:

* **Qué es:** familia "System One Models"; primer modelo "Jev": entrada de estado de programa no-estructurado → **salida tipada paralela** (no autoregresiva), entrenamiento "RLCD" (RL for Calibrated Decisions), confianza calibrada por decisión. Claims: 40–200× más rápido que LLMs frontier en tareas "System One", output tokens "gratis". *Claims de marketing, no verificables técnicamente — no es evidencia, es señal de mercado.*

* **Qué aporta a HLWSS:**
  1. **Validación económica de la dirección del proyecto** — Jev existe precisamente porque el loop autoregresivo cuesta lo que Fase 0 ya midió: ~1.2 GB de pesos releídos por token, memory-bound. Su 100× viene de eliminar el loop de raíz; el HLWSS Simulator ataca el mismo costo desde el lado de la memoria (residencia/políticas).
  2. **"Confianza con cada salida" es medible con nuestro instrumento YA** — `capture_distributions` (entropy/top1 por paso) es la versión observable de esa promesa; la extensión por capa (probes E1) mide si la confianza *intermedia* predice la decisión final. Lo que Jev afirma sin publicar, E1 lo mide con datos.
  3. **Refuerza H1/H3**: si el mercado construye modelos no-autoregresivos para decisiones, la gestión de memoria de los LLMs autoregresivos restantes se vuelve *más* crítica (los que siguen generando token-a-token son los que más sufren el memory wall).

* **Qué NO aporta:** arquitectura, fórmulas de calibración ni método de entrenamiento reproducibles → **cero cambios al diseño HLWSS**; se toma como motivación del experimento E1, no como input técnico.

### 4.2 Experimento E1 (Fase 1 candidate) — Early-exit calibrado ("política de salida")

**Idea:** si la distribución de logits en la capa k ya está "segura" (argmax/entropy), emitir el token **sin recorrer las capas restantes**. Fase 0 da la recompensa exacta: capas 20–27 ≈ 170 MB releídos por paso; 1.2 GB/token total; decode memory-bound → saltar capas tardías cuando hay confianza = tok/s real casi gratis.

**Decisiones de diseño que E1 resuelve con datos** (del análisis previo, 2026-10-02): (a) *dónde poner el corte* — probes post-hook por capa miden acuerdo argmax(cap k) vs argmax(final); (b) *penalización de calidad* — primer uso del harness `wikitext2-ppl-slice-v1` (PRD §6); (c) *calibración contra V1* — V1 se generaliza a bytes esperados dinámicos `f(capas_ejecutadas)`.

**Plan detallado:** `.claude/plans/observer-llm-early-exit.md` (E1-A probes observacionales → E1-B corte real). **No se construye en Fase 0.5**; se agenda tras ella. Prior art: CALM (Schuster et al. 2021), "logit lens".

**Salida de Fase 0.5:** trazas Parquet request-level + tabla "tamaño × contexto → tok/s, page-ins, swap" + bandwidth efectivo medido. Este número alimenta directamente los parámetros de tier del Simulator (§6 de v0.3).

**Límite honesto:** Ollama no da atribución por capa/experto (tabla §5) — Fase 0.5 mide el fenómeno agregado, no el working set interno.

***

## 5. Tabla de capacidades por backend (canónica)

| Capacidad                | Ollama                                  | oMLX API                                                            | MLX directo                 | PyTorch       |
| ------------------------ | --------------------------------------- | ------------------------------------------------------------------- | --------------------------- | ------------- |
| EXECUTE por capa         | ❌                                       | ❌                                                                   | ✅ hooks                     | ✅ hooks       |
| LOGICAL\_ACCESS expertos | ❌                                       | ❌                                                                   | ✅ hook router               | ✅ hook router |
| PHYSICAL\_TRANSFER pesos | 🟡 OS-level agregado (page-ins)         | ❌ (eager/wired)                                                     | ✅ (si HLWSS controla carga) | ❌ (simulado)  |
| PHYSICAL\_TRANSFER KV    | ❌ (no persistente)                      | 🟡 tiering hot/cold — **verificar qué expone la API antes de usar** | 🟡 según implementación     | ❌ (derived)   |
| Control de residencia    | ❌ (decide el OS)                        | ❌ (todo o nada)                                                     | ✅                           | ✅ (simulado)  |
| Rol                      | **Fase 0.5** (spill real en producción) | Referencia / verificación pendiente                                 | Fase 1+                     | **Fase 0**    |

Tarea de verificación (30 min, antes de cualquier Mode A con oMLX): levantar oMLX local, inspeccionar `/metrics` y admin, documentar qué expone del KV tier.

***

## 6. Calidad

Harness fijado: perplexity sobre slice fijo de wikitext-2 (`wikitext2-ppl-slice-v1`, versión/slice/stride fijados al implementar) + MT-Bench/Factoid-QA como segundo nivel. No bloquea Fase 0/0.5; bloquea experimentos de SKIP.

***

## 7. Detalles menores aplicados (R7)

* Tupla de V4 incluye `phase` y `component`.

* `request_id` (evento) == `run_id` (metadata), validado en el writer.

* Configs de modelos se leen de `config.json`, nunca hardcodeados.

* `phase="request"` agregado al schema para eventos agregados de servidor (Fase 0.5).

* Reformulado: en PyTorch lo "derived" es el LOGICAL\_ACCESS de KV; los transfers físicos siempre los produce el Simulator.

## 8. Riesgos (actualizado)

| Riesgo                                  | Mitigación                                                        |
| --------------------------------------- | ----------------------------------------------------------------- |
| torch.profiler en MPS sin device timing | Hooks + CPU wall; `duration_ns` nunca como device time            |
| 12 GB RAM limita MoE real               | V2 sintético en Fase 0; MoE real en Fase 1                        |
| Ollama sin atribución semántica         | Aceptado: Fase 0.5 es agregada por diseño                         |
| No-determinismo MPS rompe V4            | temperature=0, seed, tupla sin timestamps, escape determinista    |
| Volumen de eventos                      | Muestreo determinístico 1-de-N; Parquet suficiente hasta \~10⁷    |
| Confundir hit-rate con latencia         | Critical Bytes/Token como métrica primaria                        |
| Límite LOC                              | Por fase: Fase 0 ≤ 800, Fase 0.5 ≤ 200; revisión si total > 2,000 |

## 9. LOC estimado

| Módulo                                                           | LOC est.  |
| ---------------------------------------------------------------- | --------- |
| Event schema + Block Registry                                    | \~90      |
| Observer core (clock, buffer, sampling)                          | \~120     |
| PyTorch adapter (hooks, router capture, KV derived desde config) | \~170     |
| Parquet writer + metadata                                        | \~70      |
| Fidelity checks V1–V4                                            | \~140     |
| CLI                                                              | \~80      |
| **Fase 0 total (límite 800)**                                    | **\~670** |
| Ollama client + vm\_stat sampler + spill curve runner            | \~150     |
| **Fase 0.5 total (límite 200)**                                  | **\~150** |

## 10. Qué NO cambia

Filosofía Observe→Analyze→Simulate→Validate→Build; Parquet; no-objetivos de v0.1; políticas baseline; eje FIT/SPILL-KV/SPILL-WEIGHTS; restricción de calidad; jerarquía Oracle > Model-aware > LRU/Static; regla "wrap, don't rebuild".
