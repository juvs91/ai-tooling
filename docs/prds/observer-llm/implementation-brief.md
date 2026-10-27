# HLWSS — Brief de implementación Fase 0 (para Kimi Code)

> Spec canónica: `HLWSS-Observer-PRD-v0.4.md` (léela primero, es la fuente de verdad).
> Este brief solo fija orden de construcción, estructura y defaults. Si algo aquí contradice el PRD, gana el PRD.
> Hardware: Apple Silicon 12 GB · Backend Fase 0: PyTorch + MPS · Límite: ≤ 800 LOC.

***

## 0. Setup de entorno (hacer una vez)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install torch transformers pyarrow pandas accelerate
# verificación rápida:
python -c "import torch; print(torch.__version__, torch.backends.mps.is_available())"
```

* Descargar `Qwen/Qwen3-0.6B` (\~1.2 GB, no requiere token HF).

* \~4 GB de disco libres para modelo + trazas.

## 1. Estructura del repo

```
hlwss/
  hlwss/
    events.py        # Event dataclass + enums (schema v0.4)
    registry.py      # Block Registry: construcción desde modelo + sidecar JSON
    observer.py      # buffer, clock (monotonic_ns), sampling 1-de-N, capture ctx manager
    adapter_torch.py # hooks por módulo, captura de router, KV derived desde config.json
    writer.py        # Parquet + metadata sidecar (valida request_id == run_id)
    analyze.py       # bytes/token, working set por capa, reuso por bloque
    checks.py        # V1–V4 fidelity checks
    cli.py           # `hlwss run --model ... --prompts ... --out traces/`
  prompts/
    validation.json  # 5 prompts fijos (ver §2)
  traces/            # output .parquet + .json sidecars
  tests/             # tests de los fidelity checks sobre trazas sintéticas
```

## 2. Defaults ya decididos (no re-decidir)

* `prompts/validation.json`: 5 prompts en español+inglés, entre 20 y 200 tokens, deterministas (mismo archivo siempre; su sha256 va en `prompt_hash`).

* Config canónica: batch=1, temperature=0, `do_sample=False`, seed=42, `max_tokens=256`, device=`mps` si disponible si no `cpu`.

* MoE sintético para V2: arquitectura tipo OLMoE reducida — 4 capas, 8 expertos, top-2, hidden 256, pesos aleatorios (seed fija). No descargar nada.

* `KV_PAGE_TOKENS = 16`.

* Overhead target: ≤ 5% tokens/seg (V3); si falla, sampling 1-de-N automático.

* Leer TODO del `config.json` del modelo (hidden, vocab, tie\_word\_embeddings, sliding\_window, n\_kv\_heads, head\_dim). Prohibido hardcodear.

## 3. Orden de construcción (cada paso tiene su criterio de "done")

| # | Paso                                   | LOC est. | Done cuando                                                                                              |
| - | -------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------- |
| 1 | `events.py` + `registry.py`            | \~170    | Registry de Qwen3-0.6B genera blocks correctos, dedup de tied embeddings, KV paginado `L{i}.kv.p{j}`     |
| 2 | `observer.py` + `writer.py`            | \~190    | `with observer.capture(cfg) as t:` escribe parquet + sidecar cargable en pandas                          |
| 3 | `adapter_torch.py` (dense)             | \~120    | Hooks emiten EXECUTE+LOGICAL\_ACCESS por capa en un generate() de 10 tokens; embed emite fila, no bloque |
| 4 | KV derived desde config.json           | \~50     | Eventos `L{i}.kv.p{j}` con size\_bytes correcto por paso, `kv_model` en metadata                         |
| 5 | `checks.py` V1 + V3 + V4               | \~110    | V1 pasa ±10% en Qwen3-0.6B; V3 ≥95%; V4 secuencias idénticas con misma seed                              |
| 6 | MoE sintético + captura de router + V2 | \~80     | V2: hook == topk(router\_logits) al 100%                                                                 |
| 7 | `analyze.py` + `cli.py`                | \~130    | `hlwss run` produce traza; analyzer reproduce bytes/token teórico                                        |
| 8 | Reporte de validación Fase 0           | —        | Tabla V1–V4 ✅ impresa por CLI                                                                            |

**Total: \~850 LOC objetivo (recortar si pasa de 800: primero tests, nunca reglas de emisión).**

## 4. Trampas conocidas (del PRD, no redescubrir)

* MPS es asíncrono: `duration_ns` es CPU wall, NUNCA tiempo de device.

* `torch.profiler.with_modules` no funciona en eager — los hooks propios son el mecanismo, no el profiler.

* Si V4 falla por no-determinismo: `torch.use_deterministic_algorithms(True)` y documentar qué op lo rompió.

* Tied embeddings en Qwen3-0.6B: verificar `tie_word_embeddings` en config y deduplicar en V1 o la fórmula fallará \~25%.

* No medir nada con el modelo en `train()`; siempre `eval()` + `torch.no_grad()`.

## 5. Fuera de alcance de este brief

Fase 0.5 (Ollama spill curve), Simulator, políticas, FFN skipping, quality harness. No construir nada de eso aunque sea fácil.
