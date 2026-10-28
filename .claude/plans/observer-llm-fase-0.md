# Plan: HLWSS Observer Fase 0 — Observer PyTorch (vendor/hlwss/)

## Context

El PRD v0.4 (`docs/prds/observer-llm/prd-v.0.4.md`) está **aprobado para desarrollo** y el `implementation-brief.md` fija orden, estructura y defaults. Este plan expande los 8 pasos del brief en pasos atómicos ejecutables, con el detalle de cada módulo, sus criterios de aceptación y las trampas ya conocidas (para no redescubrirlas). Decidido con el usuario: **solo Fase 0** (Fase 0.5/Ollama se planea después), código en **`vendor/hlwss/`**, venv dedicado como indica el brief.

Hardware objetivo: Apple Silicon, 12 GB RAM. Backend: PyTorch + MPS. Límite: **≤ 800 LOC Fase 0** (si se excede, recortar tests primero — nunca las reglas de emisión).

Estado del entorno verificado: Python 3.11.16 ✅ · torch/transformers NO instalados globalmente (venv obligatorio) ✅ · HF cache sin Qwen3-0.6B (se descarga, ~1.2 GB, 336 GB libres) ✅ · git master limpio ✅.

---

## Paso 0 — Branch, ADR y entorno

**Branch:** usar la ya creada por el usuario, renombrada: `ex-oberver-llm` → **`ex-observer-llm`** (`git branch -m`).

**ADR (recomendado, no bloqueado por hook):** `vendor/hlwss/` NO está en los guarded prefixes del `adr-gate.sh` (solo `vendor/claude-code-proxy/` lo está), así que el hook no bloquea. Aun así, al crear un proyecto nuevo dentro de `vendor/`, se redacta **ADR-0060-observer-hlwss-en-vendor.md** documentando la decisión de ubicación y el contrato "wrap, don't rebuild" (siguiente número secuencial verificado: 0059 → 0060).

**Modelo — por qué Qwen3-0.6B y no el 9B del usuario:** Fase 0 corre el modelo como tensors fp16 en PyTorch crudo; un 9B fp16 = ~18 GB solo de pesos → no cabe en 12 GB RAM. Qwen3-0.6B (~1.2 GB) es el modelo más grande que corre cómodo y permite ver TODO por capa/tensor (ground truth). El `frob/qwen3.5-instruct:9b` (GGUF q4, mmap, KV cuantizado) es el sujeto de **Fase 0.5** (spill curve + reproducción del stall de Hermes a 64k) — Ollama no expone internals, solo métricas agregadas de servidor + OS. El caso de estudio "Hermes se muere tras varios turnos" (prefill gigante re-procesando toda la historia → KV compite con pesos → swap → stall) es exactamente lo que Fase 0.5 mide; queda fuera de este plan por decisión de scope.

1. `git branch -m ex-oberver-llm ex-observer-llm` (desde la branch; luego `git checkout ex-observer-llm`).
2. Crear `docs/adr/ADR-0060-observer-hlwss-en-vendor.md` (decisión: observer como proyecto propio en `vendor/hlwss/`, Fase 0 = PyTorch/MPS, filosofía observe-first).
3. Setup venv (una sola vez) — **por proyecto, NO en root** (decisión usuario 2026-09-27): `torch`+`transformers` son ~2-3 GB de deps específicas de hlwss; en un venv de root contagiaría a las demás apps del monorepo y acoplaría versiones. Cada app futura tendrá su propio venv:
   ```bash
   cd vendor/hlwss
   python3.11 -m venv .venv && source .venv/bin/activate
   pip install torch transformers pyarrow pandas duckdb
   pip install pytest   # dev: tests/ (fuera del runtime)
   python -c "import torch; print(torch.__version__, torch.backends.mps.is_available())"
   ```
   (sin `accelerate` — no hace falta: el device placement es manual (`model.to(device)`), no `device_map` auto)
4. Descargar `Qwen/Qwen3-0.6B` (~1.2 GB, sin token HF).
5. `.gitignore` en `vendor/hlwss/`: `.venv/`, `traces/`, `__pycache__/`, `*.parquet`.
6. Tras la primera corrida válida, congelar `requirements.txt` con `pip freeze` — V3 (overhead %) es sensible a versiones de torch; el `backend_version` va en cada metadata para trazabilidad.

**Done cuando:** MPS disponible según el check, modelo descargado, branch renombrada, ADR-0060 creado.

**Antes de escribir código (operativo, sesión de implementación):**
1. Reescribir el `task-scope.json` de la sesión a `mode=build`, `allowed_patterns: ["vendor/hlwss/", "docs/adr/"]` — el scope actual (mode=full heredado del bootstrap de análisis) no restringe, pero declararlo build deja el gate explícito y protege contra ediciones fuera del proyecto (`scope-gate.sh`).
2. Constraints heredados de CLAUDE.md que aplican a cada archivo nuevo: **imports a nivel de módulo, nunca dentro de funciones**; **sin stubs** (`verify-implementation.sh` rechaza `pass`/`TODO`/`NotImplemented` en .py editados); ritmo de verificación — tests cada ~8 edits (`edit-drift-detector.sh` advierte a 8/15/25).

---

## Estructura del repo

```
vendor/hlwss/
  pyproject.toml     # empaquetado minimal: entry point `hlwss` → hlwss.cli:main (fuera del presupuesto LOC)
  hlwss/
    events.py        # Event dataclass + enums (schema v0.4)          ~40 LOC
    registry.py      # Block Registry desde config.json + sidecar     ~130
    observer.py      # clock, buffer, sampling 1-de-N, capture ctx     ~120
    adapter_torch.py # hooks, router capture, KV derived   ~170 (+40-60 flag)
    writer.py        # Parquet + metadata sidecar                      ~70
    checks.py        # V1–V4 fidelity checks                           ~150
    analyze.py       # bytes/token, working set, reuso                 ~80
    cli.py           # `hlwss run --model ... --prompts ... --out ...` ~80
  prompts/validation.json   # 5 prompts fijos (es+en, 20–200 tokens, deterministas)
  tests/                    # FUERA del límite 800: unitarios de funciones puras
                            # (registry fórmulas, kv_bytes_per_step) + checks sobre trazas sintéticas
                            # si hay recorte: caen tests de integración, NUNCA los unitarios de fórmulas
  traces/                   # traces/{run_id}/events.parquet + .json (gitignored)
```

**Presupuesto LOC:** suma de módulos **~840**; con `--capture-distributions` (los +40-60 viven dentro de adapter_torch.py) queda en **~880-900**. Límite duro 800 → plan de recorte si se cruza: (1) tests primero, (2) distribuciones a versión mínima (entropía + top-1, ~25 LOC), (3) nunca reglas de emisión.

---

## Diagrama de arquitectura

```
 prompts/validation.json (5 fijos, sha256 → prompt_hash)
            │
 config.json            │   cfg: batch=1, temp=0, do_sample=False,
 (HF, leído,            ▼    seed=42, max_tokens=256, mps|cpu
  nada hardcodeado)  ┌─────────────┐
        │            │   cli.py    │  subcomandos: run / checks
        │            └──────┬──────┘
        ▼                   │ carga modelo: eval() + no_grad(), device
  ┌────────────┐            ▼
  │ registry.py│      ┌──────────────────────────────────┐
  │ Block      │      │     adapter_torch.py             │
  │ Registry   │─────►│  pares de hooks forward por      │──► EXECUTE + LOGICAL_ACCESS
  │ + sidecar  │ ids  │  módulo poseído de bloque        │    (embed = 1 fila)
  └────────────┘      │  hook router MoE (logits crudos) │──► V2
        ▲             │  KV derived desde config         │──► source="derived" L{i}.kv.p{j}
        │             │  distribuciones (flag)           │──► 2º dataset Parquet
        │             └──────┬───────────────────────────┘
        │                    │ observer.emit(Event) — 1 por acción (§2.3)
        │                    ▼
        │             ┌──────────────┐   reloj único: time.monotonic_ns()
        │             │  observer.py │   buffer en RAM + sampling 1-de-N
        │             │ capture(ctx) │   (activa si V3 > 5% overhead)
        │             └──────┬───────┘   run_id = uuid4
        │                    │ __exit__ → volcado
        │                    ▼
        │             ┌──────────────┐   events.parquet (pyarrow)
        └────────────►│  writer.py   │   distributions.parquet (solo flag)
                      │ + sidecar    │   sidecar.json (registry + metadata §2.5)
                      └──────┬───────┘   valida request_id == run_id
                             ▼
               ┌───────────────────────────┐
               │  analyze.py → DuckDB      │──► bytes/token, working set/layer,
               │  (SQL in-process sobre    │    reuso/block; reproduce números
               │   Parquet) + pandas       │    teóricos de V1
               │  checks.py → V1–V4        │──► reporte tabla ✅/❌
               └───────────────────────────┘
```

**Flujo de un token en decode:** hook pre-forward guarda `t0=monotonic_ns` → módulo corre → hook post-forward emite `EXECUTE + LOGICAL_ACCESS` (mismo `timestamp_ns` base, `duration_ns = monotonic_ns − t0` = CPU wall, nunca device) → adapter emite KV derivado de la página correspondiente → si flag, captura distribución → `token_pos += 1`.

---

## Detalle por módulo

### 1. `events.py` + `registry.py` (~170 LOC) — *Done: Registry de Qwen3-0.6B genera blocks correctos, dedup de tied embeddings, KV paginado `L{i}.kv.p{j}`*

**`events.py`:** `@dataclass Event` con los 11 campos canónicos del PRD §2.1 (`timestamp_ns`, `request_id`, `token_pos`, `phase`, `layer`, `component`, `block_id`, `event_type`, `size_bytes`, `duration_ns`, `source`). Constantes validadas contra el PRD (segunda revisión hecha, completas):

| Enum | Valores | Qué significa |
|---|---|---|
| `phase` | `prefill` \| `decode` \| `request` | etapa del ciclo: lectura del prompt en paralelo / generación token-a-token / agregado por corrida (solo Fase 0.5) |
| `component` | `ATTN` \| `FFN` \| `EXPERT` \| `KV` \| `EMBED` \| `LM_HEAD` \| `OTHER` | qué parte de la arquitectura posee el tensor |
| `event_type` | `EXECUTE` \| `LOGICAL_ACCESS` \| `PHYSICAL_TRANSFER` \| `PREFETCH` \| `EVICT` \| `SKIP` | qué pasó: cómputo / acceso lógico / movimiento físico RAM↔SSD / carga anticipada / expulsión / salto de capa (post-Fase 0). Los 3 últimos no se emiten en PyTorch — los produce el Simulator |
| `source` | `hook` \| `profiler` \| `server_metric` \| `os_metric` \| `derived` \| `simulated` | quién midió: hooks propios / torch.profiler (reservado) / API Ollama / `vm_stat` / fórmula (KV) / Simulator |

**`registry.py`:**
- Lee **todo** del `config.json` real del modelo: `hidden_size`, `vocab_size`, `tie_word_embeddings`, `sliding_window`, `num_hidden_layers`, `num_attention_heads`, `num_key_value_heads`, `intermediate_size`, dtype de los pesos (`torch_dtype` — **Qwen3-0.6B es bf16, no fp16 como dice el PRD**; ambos 2 bytes así que V1 es idéntico, pero `quant` en metadata debe registrar el dtype real leído, nunca asumido); **si el config es MoE**: `num_experts`, `num_experts_per_tok`, `moe_intermediate_size`. **Prohibido hardcodear** (PRD §3 nota de implementación).
- `head_dim = hidden_size / num_attention_heads` si no viene explícito.
- Bloques: `embed`, `L{i}.attn`, `L{i}.ffn`, `lm_head`, `L{i}.kv`; para MoE: `+ L{i}.expert.{j}`, `L{i}.shared_expert`.
- Tamaños: `attn` = Q,K,V,O proj; `ffn` = gate,up,down; `embed` = vocab × hidden × dtype_bytes.
- **Tied embeddings:** si `tie_word_embeddings=true`, registrar una sola vez; el otro apunta vía `tied_with`. Es compartición de pesos entre EMBED (token→vector) y LM_HEAD (vector→logits) — en Qwen3-0.6B es el bloque más grande (~300+ MB); sin dedup V1 falla ~25% (trampa §4 del brief).
- **Normas RMSNorm, rotary y biases FUERA del Registry** (<1% bytes, absorbidos por tolerancia V1).
- **KV paginado:** `L{i}.kv` → secuencia `L{i}.kv.p{j}` con `KV_PAGE_TOKENS = 16` (default vLLM, configurable por corrida y registrado en metadata). Página = `2 × n_kv_heads × head_dim × dtype_bytes × 16`. El sidecar lleva **ambos** ids: las páginas (unidad de residencia para el Simulator) **y** una entrada agregada `L{i}.kv` (`aggregated: true`, `derived: true`, `size_bytes` = bytes para la `context_window` completa) — es el id que los eventos de Fase 0 referencian, para que toda traza apunte a bloques registrados.
- **`kv_model` como mapa por capa** (mejora sobre el string global del PRD, ~0 LOC extra): cierra el hueco de atención **híbrida intercalada** (Mistral/Gemma: capas alternas SWA/full). Casos: `full` \| `swa:{W}` (con `min(context_len, W)`) \| `quant:{type}`. GQA/MQA ya entran vía `n_kv_heads` en la fórmula. **MLA (DeepSeek: KV latente `kv_lora_rank + rope_dim`) rompe la fórmula** — documentado como fuera de alcance Fase 0, Fase 1+.
- Sidecar JSON: `block_id → {layer, component, size_bytes, tied_with}`.

**Detalle técnico:**
```python
@dataclass(frozen=True, slots=True)
class Event: ...                      # 11 campos §2.1; __post_init__ valida enums contra frozensets
def to_row(e: Event) -> dict          # dict plano para pyarrow (11 columnas)

class BlockRegistry:
    @classmethod
    def from_config(cls, cfg, dtype_bytes, kv_page_tokens=16) -> BlockRegistry  # cfg = AutoConfig.from_pretrained(...) — leído del config.json real del modelo
    def size(self, block_id: str) -> int
    def kv_pages(self, layer: int, upto_token: int) -> list[str]   # páginas [0 .. ceil(upto_token/16)]
    def sidecar(self) -> dict[str, BlockMeta]
```
Fórmulas de tamaño (todo desde cfg, sin hardcode): `attn = 2·hidden·n_heads·head_dim (q,o) + 2·hidden·n_kv_heads·head_dim (k,v)` — **no** `hidden²`: en Qwen3-0.6B `n_heads·head_dim = 16×128 = 2048 ≠ hidden 1024`, la forma corta calcularía mal el bloque más grande; `ffn (SwiGLU) = 3·hidden·intermediate` (gate,up,down); `embed = vocab·hidden·dtype_bytes`; `kv_página = 2·n_kv_heads·head_dim·dtype_bytes·16`.

### 2. `observer.py` + `writer.py` (~190 LOC) — *Done: `with observer.capture(cfg) as t:` escribe parquet + sidecar cargable en pandas*

**`observer.py`:**
- Único reloj: `time.monotonic_ns()`.
- Buffer en memoria de Events; `emit()` lo usan los hooks.
- **Muestreo determinístico 1-de-N por `token_pos`** (activado automáticamente si V3 supera 5% overhead).
- Context manager `capture(cfg)`: abre run, asigna `run_id=uuid4()`, al salir vuelca al writer.
- Hooks registran `duration_ns` como **CPU wall del hook** — nunca tiempo de device (MPS es asíncrono).

**`writer.py`:** Parquet vía pyarrow + sidecar JSON de metadata (schema §2.5, **todos** los campos: `schema_version, run_id, timestamp_utc, model_id, quant, n_layers, n_experts=0` (dense), `backend="pytorch"`, `backend_version=torch.__version__`, `hardware, os, ram_total_gb=12, ram_available_gb, swap_used_at_start_gb` — estos dos capturados una vez al inicio sin deps nuevas: `os.sysconf` + `vm_stat` vía subprocess —, `ssd_model_path, seed=42, temperature=0.0, do_sample=false, prompt_hash` (sha256 de validation.json), `max_tokens, context_window, kv_model` (mapa por capa), `quality_harness="wikitext2-ppl-slice-v1"` (constante declarada aunque el harness corre post-Fase 0), `clock="time.monotonic_ns"`). **Valida `request_id == run_id`** (R7). Con `--capture-distributions` activo escribe el dataset auxiliar `distributions` en el mismo Parquet.

**Detalle técnico:**
```python
class Observer:
    def capture(self, cfg: RunConfig) -> ContextManager[Trace]   # Trace expone run_id, events, distributions
    def emit(self, e: Event) -> None        # append a buffer; descarta si sampling activo y no pasa
    def _passes_sampling(self, token_pos) -> bool   # token_pos % N == 0; N=1 default, N>1 solo si V3 falla
class Trace: run_id: str; events: list[Event]; dists: list[DistRow]   # todo en RAM hasta __exit__

# writer.py
def write(trace, registry_sidecar, metadata, out_dir) -> WriteResult:  # 3 paths bajo traces/{run_id}/: events.parquet, distributions.parquet|None, metadata.json
    # events.parquet: pyarrow.Table de [to_row(e) for e in trace.events]  (orden de inserción = orden canónico)
    # distributions.parquet (solo si trace.dists): cols (run_id, token_pos, layer, component, metric, value)
    # metadata.json: metadata §2.5 + registry sidecar
    # assert metadata["run_id"] == trace.run_id  (validación R7)
```

### 3. `adapter_torch.py` — dense (~120 LOC; +~50 del KV en paso 4 vive en este mismo archivo; +40-60 del flag distribuciones) — *Done: hooks emiten EXECUTE+LOGICAL_ACCESS por capa en un generate() de 10 tokens; embed emite fila, no bloque*

- Registra forward hooks por módulo poseído de bloque. Regla de emisión §2.3: **1 EXECUTE + 1 LOGICAL_ACCESS por bloque, mismo timestamp base**.
- `size_bytes` del evento = **bytes realmente tocados**: attn/ffn/lm_head → tamaño completo del bloque (un matmul —`salida = x @ W`, el núcleo de toda capa lineal— lee todo el peso); **embed → una fila** (`hidden_size × dtype_bytes`) en prefill y decode — nunca el bloque completo (hallazgo R1).
- **Tracking prefill vs decode:** `phase` + `token_pos` — forward del prompt → `prefill, token_pos=None`; pasos autoregresivos → `decode, token_pos=0,1,2,...`. Los eventos KV derivados siguen la misma lógica (prefill toca `ceil(prompt_len/16)` páginas nuevas por capa; decode, 1 página nueva cada 16 tokens).
- Modelo siempre `eval()` + `torch.no_grad()` (trampa §4: nada en `train()`).
- Los hooks propios son el mecanismo — `torch.profiler.with_modules` no funciona en eager.
- **Sin `torch.use_deterministic_algorithms` aún** — solo si V4 falla (ver paso 5).

**Captura de distribuciones (decisión del usuario 2026-09-27): flag `--capture-distributions`, default off (+~40-60 LOC).** En cada paso de decode, guarda en un **segundo dataset Parquet** `distributions` (clave `(token_pos, layer, component)`, sin tocar el schema de eventos): entropía de los logits finales + top-1/top-5 probabilidades (LM_HEAD), y norm L2 de salidas de attn/ffn en el camino crítico. Racional: `temperature=0` sigue siendo la config canónica (V4/V1), pero la forma de la distribución interna es información barata que el Simulator de fases posteriores agradecerá. El router MoE ya guarda logits crudos (paso 6) — mismo patrón.

**Detalle técnico:**
- **Registro de hooks:** recorrer `model.named_modules()` y mapear nombres a `block_id` por convención del modelo (Qwen3: `model.embed_tokens → embed`, `model.layers.{i}.self_attn → L{i}.attn`, `model.layers.{i}.mlp → L{i}.ffn`, `lm_head → lm_head`). El mapa nombre→block_id sale del Registry, no se hardcodea por nombre suelto.
- **Duración real por módulo:** par pre/post hook — pre guarda `t0 = time.monotonic_ns()` en dict por handle; post emite con `duration_ns = monotonic_ns − t0` (CPU wall).
- **Embed como fila:** el pre-hook de embed captura `input_ids`; la fila tocada es `input_ids[-1]` (último token) → `size_bytes = hidden_size × dtype_bytes`. Regla PRD §2.3 literal: **una fila en prefill y decode** (físicamente prefill lee `prompt_len` filas, pero la diferencia ~400 KB vs ~1.2 GB/paso la absorbe la tolerancia 10% de V1 — comentario en código, no regla especial).
- **Modo off para V3:** con `HLWSS_OFF=1` el adapter **no registra ningún hook** (passthrough puro) — si no, V3 mediría overhead de todos modos.
- **Loop de generación manual, no `generate()`** de HF: prefill = un forward con el prompt completo (`phase=prefill, token_pos=None`); luego loop de `max_tokens` forwards agregando 1 token (`phase=decode, token_pos=t`). Razón: control total del reloj y del sampling greedy (`argmax(logits[-1])`), sin black-box de HF entre los hooks.
- **Emisión de KV (volumen controlado):** el adapter emite **1 evento agregado por capa por paso** con `block_id=L{i}.kv` (entrada agregada del sidecar) y `size_bytes` exacto (fórmula §2.4 con `C_actual = prompt_len + token_pos`). Materializar eventos por página (4096 págs × 28 capas × 256 tokens) explotaría el buffer; las páginas `L{i}.kv.p{j}` viven en el sidecar para el Simulator.

### 4. KV derivado desde config.json (~50 LOC) — *Done: eventos `L{i}.kv.p{j}` con size_bytes correcto por paso, `kv_model` en metadata*

- Fórmula por capa desde config (PRD §2.4): atención completa `2 × n_kv_heads × head_dim × dtype_bytes × context_len`; sliding window si existe: `context_len → min(context_len, W)`.
- Eventos KV **sintéticos** con `source="derived"` — es el LOGICAL_ACCESS de KV; los transfers físicos siempre los produce el Simulator (post-Fase 0).
- En decode, por cada `token_pos` se toca 1 página nueva (cada 16 tokens); en prefill, páginas hasta el prompt.

**Detalle técnico:** implementado como función pura desde config, separada del adapter para testearla sola:
```python
def kv_bytes_per_step(cfg, dtype_bytes, layer: int, context_tokens: int) -> int:
    """Bytes de KV realmente leídos por la atención en un paso con contexto de
    `context_tokens` tokens (prompt_len + token_pos)."""
    n_kv, hd = cfg.num_key_value_heads, head_dim(cfg)
    w = getattr(cfg, "sliding_window", None)
    eff = min(context_tokens, w) if w else context_tokens   # swa:{W}
    return 2 * n_kv * hd * dtype_bytes * eff
# Esperado V1 por paso = Σ bloques no-KV por capa (embed=fila, dedup tied) + Σ_capas kv_bytes_per_step(...)
```

### 5. `checks.py` — V1 + V3 + V4 (~110 LOC) — *Done: V1 ±10% en Qwen3-0.6B; V3 ≥95%; V4 secuencias idénticas con misma seed*

Config canónica: batch=1, temperature=0, `do_sample=False`, seed=42, `max_tokens=256`, device `mps` si disponible si no `cpu`, prompts `prompts/validation.json`.

- **V1 (fidelidad de bytes):** `expected = Σ bytes bloques no-KV (dedup tied, embed como fila) + KV derivado por paso` vs `measured = Σ size_bytes de LOGICAL_ACCESS por token_pos`. Criterio `|measured−expected|/expected ≤ 10%` promediado.
- **V3 (overhead):** tok/s con Observer ≥ 95% de baseline run idéntico sin Observer. Si falla → sampling 1-de-N automático.
- **V4 (reproducibilidad):** misma seed+prompt → secuencia idéntica de la tupla `(event_type, block_id, layer, component, phase, token_pos)` — **sin timestamps** (R7). Si MPS rompe determinismo: `torch.use_deterministic_algorithms(True)` + documentar qué op lo rompió.

**Detalle técnico:** (V1 y V4 corren siempre con sampling N=1 — el sampling 1-de-N es solo para V3/overhead en producción. La metadata registra `sample_n`; si `sample_n > 1`, checks V1/V4 **abortan con error claro** en vez de fallar silenciosamente por traza incompleta)
```python
# checks.py — todos contra los Parquet, sin re-correr el modelo salvo V3/V4
V1(expected_per_step, traces_dir) -> Ratio:
    # criterio SOLO sobre pasos de decode (token_pos IS NOT NULL): es donde el
    # PRD define "KV derivado por paso". Prefill (token_pos NULL) se valida aparte:
    prefill_ok = assertion separada: cada bloque exactamente 1 LOGICAL_ACCESS
                 + 1 evento KV agregado con C = prompt_len
    measured = duckdb.sql("""SELECT token_pos, SUM(size_bytes) AS b
        FROM 'traces/*.parquet' WHERE event_type='LOGICAL_ACCESS'
          AND token_pos IS NOT NULL
        GROUP BY token_pos ORDER BY token_pos""").df()
    return abs(measured.b - expected_per_step) / expected_per_step   # criterio ≤ 10% promedio
V3(model, cfg):  # dos corridas: observer desactivado (env HLWSS_OFF=1) vs activo
    return tokps(on) / tokps(off)                          # criterio ≥ 0.95
V4(traces_a, traces_b):
    # orden canónico = orden físico del Parquet (inserción; pyarrow lo preserva).
    # NO ORDER BY ts: EXECUTE+LOGICAL_ACCESS comparten timestamp base (§2.3) → ties.
    def seq(p):
        df = pd.read_parquet(p)   # preserva orden de fila = orden de inserción
        return list(zip(df.event_type, df.block_id, df.layer,
                        df.component, df.phase, df.token_pos))
    return sha256(repr(seq(a)).encode()) == sha256(repr(seq(b)).encode())
```
V1 corre **solo contra trazas ya escritas** (DuckDB) — barato de re-ejecutar durante desarrollo.

### 6. MoE sintético + router capture + V2 (~80 LOC) — *Done: V2 hook == topk(router_logits) al 100%*

- Arquitectura tipo OLMoE reducida: 4 capas, 8 expertos, top-2, hidden 256, **pesos aleatorios con seed fija**. Nada se descarga (decisión R4: ningún MoE real cabe en 12 GB; MoE real va a Fase 1).
- Router: 1 LOGICAL_ACCESS por experto seleccionado + log auxiliar con logits crudos (para V2).
- **V2:** comparación `hook == topk(router_logits)` al 100% — valida que el hook captura decisiones del router (depende de arquitectura, no de pesos).

**Detalle técnico — arquitectura MoE real escalada (literal al PRD §3 V2):** `Qwen3MoeConfig` tiny (4 capas, 8 expertos, `num_experts_per_tok=2`, hidden 256, `moe_intermediate_size` pequeño) → `Qwen3MoeForCausalLM(cfg)` **sin `from_pretrained`** — la inicialización de from_config ya da pesos aleatorios bajo `torch.manual_seed(42)`; nada se descarga. Ventaja: el Registry construye `L{i}.expert.{j}` / `L{i}.shared_expert` por la misma `from_config` que el denso, y el enrutado es el real de la arquitectura (lo que V2 quiere validar). El adapter agrega el mapeo de nombres MoE: `model.layers.{i}.mlp.gate → router`, `model.layers.{i}.mlp.experts.{j} → L{i}.expert.{j}`, `model.layers.{i}.mlp.shared_expert → L{i}.shared_expert`.
```python
class RouterHook:  # registrado en el gate del MoE
    def __call__(self, module, args, output):
        logits = output                       # logits crudos pre-softmax
        k = module.config.num_experts_per_tok # NO hardcodear top-2
        sel = logits.topk(k=k, dim=-1).indices
        observer.emit(... component=EXPERT, event_type=LOGICAL_ACCESS, size_bytes=expert_size ...)
        trace.router_logits.append((token_pos, logits.detach().cpu()))
# V2: for (token_pos, logits) in trace.router_logits:
#        assert set(hook_seleccionados(token_pos)) == set(logits.topk(k).indices)
```

### 7. `analyze.py` + `cli.py` (~130 LOC) — *Done: `hlwss run` produce traza; analyzer reproduce bytes/token teórico*

- `analyze.py`: bytes/token, working set por capa, reuso por bloque. **Motor: DuckDB embebido** (decisión usuario 2026-09-27) — el "mini motor" sin servidor: SQL in-process directo sobre los Parquet (`SELECT layer, SUM(size_bytes) FROM 'traces/*.parquet' GROUP BY layer`); pandas para el reporte V1–V4 y vistas rápidas; CSV como export opcional para inspección manual. Sin NoSQL: los eventos son tabulares/columnares puros, fit natural de OLAP embebido. Debe **reproducir los números teóricos** confirmados por cálculo a mano en V1.
- `cli.py`: `hlwss run --model ... --prompts ... --out traces/` — corre generate bajo observación y vuelca traza + metadata.

**Detalle técnico:**
```python
# analyze.py — DuckDB in-process; reproduce los números teóricos de V1 por construcción
BYTES_PER_TOKEN = """SELECT phase, token_pos, SUM(size_bytes)/1e6 AS mb
    FROM events GROUP BY phase, token_pos ORDER BY token_pos"""
WORKING_SET = """SELECT token_pos, COUNT(DISTINCT block_id) AS blocks,
    SUM(size_bytes) FILTER (WHERE first_seen) AS new_bytes
    FROM (SELECT *, MIN(ts) OVER (PARTITION BY block_id) = ts AS first_seen
          FROM events) GROUP BY token_pos"""          # set de bloques vivos por paso
REUSE = """SELECT block_id, COUNT(*) AS touches, SUM(size_bytes) AS total_bytes
    FROM events WHERE event_type='LOGICAL_ACCESS' GROUP BY block_id ORDER BY total_bytes DESC"""
# cli.py — argparse con subcomandos:
#   hlwss run   --model Qwen/Qwen3-0.6B --prompts prompts/validation.json --out traces/ [--capture-distributions] [--sample-n N]
#   hlwss check --traces traces/ [--v V1|V3|V4|all]     # imprime tabla ✅/❌
```

### 8. Reporte de validación Fase 0

CLI imprime tabla V1–V4 ✅/❌ + LOC real por módulo vs presupuesto. Salida de Fase 0 (PRD §3): V1/V3/V4 ✅ en Qwen3-0.6B, V2 ✅ en MoE sintético, analyzer reproduce bytes/token + working set + reuso.

### 9. Mejora al Task Mode Protocol de CLAUDE.md (opcional, con aprobación del usuario — CLAUDE.md queda fuera de `allowed_patterns`)

Agregar al bloque "Scope Discipline" la línea: *"**Cadencia de verificación (mode=build):** correr los tests unitarios al menos cada ~8 edits; el hook edit-drift-detector advierte en 8/15/25, pero la regla debe existir como texto porque este protocolo se copia a proyectos sin hooks instalados."* Justificación: la cadencia hoy solo vive en el hook, no en instrucción para el modelo.

---

## Verificación end-to-end

1. Por cada paso 1–7: correr su criterio "done" específico (arriba) antes de avanzar — el orden del brief es dependiente.
2. **Ritmo de verificación:** con ≥8 edits sin test run, `edit-drift-detector.sh` advierte — correr tests al terminar cada paso, no al final.
3. Suite final (venv activado, desde `vendor/hlwss/`):
   ```bash
   python -m hlwss.cli run --model Qwen/Qwen3-0.6B --prompts prompts/validation.json --out traces/
   python -m hlwss.cli check --traces traces/ --v all   # tabla V1–V4 ✅/❌
   ```
4. Checks de hooks: sin funciones stub (`verify-implementation.sh`), sin errores en `.claude/quality-state/` (`quality-enforce.sh`).
5. **Cierre de tarea:** `./scripts/task-verify.sh` antes de reportar completa; `ai-notes/AI_LEARNING.md` actualizado con decisiones técnicas y errores encontrados (Feedback Loop de CLAUDE.md).

## Qué NO se construye (fuera de alcance, aunque sea fácil)

Fase 0.5 (Ollama spill curve), Simulator, políticas de residencia, FFN skipping, quality harness wikitext2 — todos post-Fase 0 según PRD/brief. `PHYSICAL_TRANSFER/PREFETCH/EVICT` no se emiten en PyTorch; los produce el Simulator.

## Riesgos y trampas (del PRD/brief — no redescubrir)

| Trampa | Mitigación ya fijada |
|---|---|
| MPS asíncrono: `duration_ns` no es device time | CPU wall only, documentado en schema |
| Tied embeddings inflan V1 ~25% | dedup vía `tie_word_embeddings` (registry) |
| No-determinismo MPS rompe V4 | temperature=0 + seed + tupla sin timestamps + escape determinista |
| `torch.profiler` no sirve en eager | hooks propios son el mecanismo |
| Volumen de eventos | sampling 1-de-N determinístico; Parquet hasta ~10⁷ |
| LOC > 800 | recortar tests de integración primero, nunca reglas de emisión ni unitarios de fórmulas |
| Dependencias de versiones rompen V3 silenciosamente | `requirements.txt` congelado + `backend_version` en cada metadata |
| `transformers` cambia nombres de módulos entre versiones (mapeo nombre→block_id) | mapeo vive en el Registry por convención + test unitario que lo fija contra la versión instalada |

---

## Hacia Fase 1 — selección de engine (nota, decisión pendiente)

Contexto añadido por el propietario: el proyecto debe **portar entre Mac y Linux**. Lo que le gustó de Ollama = el lazy-loading de tensores vía **mmap** (el OS fautea páginas del GGUF bajo demanda y las evicta bajo presión — eso *es* PHYSICAL_TRANSFER a nivel OS, y Fase 0.5 lo mide con page-ins).

Matriz de opciones para el **engine instrumentado** (observer inline + control de residencia):

| Criterio | llama.cpp (motor de Ollama) | MLX directo | PyTorch (MPS/CUDA) |
|---|---|---|---|
| Portabilidad Mac↔Linux | ✅✅ (Metal/CUDA/Vulkan/CPU) | ❌ Mac-only en la práctica (Linux experimental) | ✅✅ (MPS en Mac, CUDA en Linux, **mismo código**) |
| Hooks / acceso por tensor | ❌ C++; solo vía API/server externo | ✅ Python `nn.Module`, árbol de tensores accesible | ✅ hooks nativos (ya es Fase 0) |
| Control de residencia | parcial (`--no-mmap`, offload layers) — decide el OS | ✅ si hlwss controla la carga (tabla §5 PRD) | ✅ simulado/impl. propia |
| El lazy-loading tipo Ollama que te gustó | ✅ nativo (mmap GGUF) | hay que construirlo (memmap de safetensors) | hay que construirlo (memmap de safetensors) |
| Ecosistema/modelos | GGUF gigante | safetensors vía mlx-community | HF nativo |

Lectura: **llama.cpp gana como línea base de producción portable** (por eso es Fase 0.5: el spill real de hoy). **MLX gana como vehículo de experimentos en el Mac** (unified memory, acceso Python directo) pero no porta. **PyTorch es el único engine instrumentado que porta Mac↔Linux con el mismo código — y Fase 0 ya lo es.** O sea: la decisión no es "llama.cpp vs MLX" para *todo*, sino qué rol lleva cada uno.

**Decisión del propietario (2026-09-28): Fase 1 = PyTorch portable** — el engine instrumentado (observer inline + control de residencia) crece desde el código de Fase 0: memmap de safetensors (el lazy-loading tipo Ollama construido en Python, con PHYSICAL_TRANSFER/PREFETCH/EVICT reales emitidos por primera vez), políticas de residencia, mismo código MPS (Mac) / CUDA (Linux). Roles finales: llama.cpp/Ollama = línea base de producción portable (caja negra, Fase 0.5 + validación final de políticas); PyTorch = engine instrumentado portable; MLX queda descartado para Fase 1 (Mac-only contradice el requisito de portabilidad) — oMLX retorna solo como referencia de diseño para tiered KV, no como backend.
