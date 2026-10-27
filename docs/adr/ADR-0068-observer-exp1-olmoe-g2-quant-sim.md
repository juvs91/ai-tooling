# ADR-0068: Exp1 — Falsación MoE con OLMoE + G2 — Simulación de escenarios de cuantización

- **Estado:** aceptado (staged 2026-10-20, branch `ex-observer-llm`)
- **Contexto:** ADR-0060–0065 (observer/simulador), ADR-0066 (política
  adaptativa), ADR-0067 (G1 gemma3n). Las conclusiones MoE (valle de spill,
  pin por frecuencia top-90%, 5 NO-GOs de prefetch) reposan sobre un único
  modelo (Qwen1.5-MoE-A2.7B); la conclusión G1 (acantilado 3n) abrió la
  pregunta práctica de si cuantizar domina a la política de residencia.
- **Decisión:** (1) trazar **OLMoE-1B-7B-0924-Instruct** como segundo MoE
  de falsación, con un único cambio de código (V1 MoE-aware en checks.py);
  (2) añadir al simulador un flag `--scale-component` para reescalar
  tamaños de bloque por familia y medir escenarios de cuantización (G2,
  bytes-only) sobre la traza gemma3n existente.

## Exp1 — Elección del segundo MoE

Criterios, en orden de peso:

1. **Distancia arquitectónica** — falsar con un clon aporta poco. OLMoE: 16
   capas, 64 expertos top-8 (vs 60/4), sin shared_expert, sin GQA (16/16),
   emb/lm_head no atados, vocab 50k, ~13.8GB bf16 (12.9GB en expertos).
   Fracción activa por token 12.5% vs 6.7% (Qwen) → valle predicho más
   somero pero presente.
2. **Compatibilidad de instrumentación** — el naming OLMoE en transformers
   5.x (`model.layers.{i}.self_attn`, `.mlp.gate`, `.mlp.experts` fusionado)
   resuelve por el dispatch existente del registry (basado en substrings,
   no en clase) y el path fusionado del adapter (el router emite la
   selección). Carga vía `AutoModelForCausalLM`. **Cero cambios en
   registry/adapter/cli** — verificado contra el código y contra el source
   local de transformers.
3. **Ungated** — allenai/OLMoE-1B-7B-0924-Instruct sin gate de licencia.

### Cambio de código único: V1 MoE-aware (checks.py)

El V1 actual suma `touch_size(L{i}.ffn)` por capa; para MoE el contenedor
`mlp` mapea a None y lo que se emite son topk expertos (+ shared_expert en
Qwen1.5). Qwen1.5 solo pasaba V1 por coincidencia numérica (ffn ==
shared_expert en tamaño). La fórmula nueva para MoE: por capa
`attn + topk × expert + (shared si existe) + kv`; conteo de prefill
`per_layer = 1 + topk + (1 si shared)`. Se valida con fixtures sintéticos,
no re-trazando Qwen1.5.

### Riesgo memoria (documentado)

13.8GB bf16 en máquina de 16GB → swap probable durante la traza;
mitigación: nada más corriendo, fallback `--device cpu` si MPS OOM. La
traza es para anatomía de acceso, no para benchmark de latencia.

### Criterio de falsación (marcado antes de medir)

- **P2 es la predicción crítica:** el valle de spill existe para OLMoE
  (hlwss-adaptive > lru en [0.85, 0.95]×WS). Si P2 falla, las conclusiones
  MoE eran artefacto de Qwen1.5 y se reescribe el story del proyecto.
- P1 (demanda ~2.3–2.5GB/token), P3 (sin predictibilidad cross-token),
  P4 (concentración por frecuencia) acompañan.

## Exp2 — G2: cuantizar vs política

- **Alcance:** bytes-only. El flag `--scale-component COMP:FACTOR`
  (repetible; `weights` = todos los bloques no derivados) reescala
  `size_bytes` del registry entre `load_run` y `simulate`. KV queda bf16
  (0.4% de la demanda). **La dimensión calidad (correr el modelo
  cuantizado real) es G2b, futura.**
- **Matiz de granularidad (heredado de A4):** el sim transfiere bloques
  enteros; cuantizar PLE aquí modela "política que transfiere bloques
  cuantizados", no la esparcidad de filas (esa ya la explota el OS a 4KB).
  La comparación honesta es contra el baseline bf16 a igual budget.
- **Escenarios:** S1 PLE int4 (~5.76GB), S2 PLE+FFN int4 (~3.51GB), S3
  todo int4 (~2.21GB); budgets 2–8GB × todas las políticas.

## Consecuencias

- checks.py: rama MoE en `expected_bytes_per_step` y conteo de prefill
  (~15 LOC); simulate.py: `scale_blocks` (~15 LOC); cli.py: flag
  (~12 LOC); tests (~65 LOC). Total ~107 LOC → ~2,481/2,500 del cap
  ADR-0064. Sin bump de cap.
- Entregables: `ai-notes/findings/exp1-olmoe-falsificacion.md` (con
  veredicto de falsación explícito) y `ai-notes/findings/g2-cuantizar-vs-
  politica.md` (fit thresholds por escenario + orden de prioridades
  cuantizar-vs-política).
