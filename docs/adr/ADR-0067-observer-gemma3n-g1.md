# ADR-0067: G1 — Instrumentación Gemma 3n y anatomía del patrón de acceso (PLE)

- **Estado:** aceptado (staged 2026-10-14, branch `ex-observer-llm`)
- **Contexto:** ADR-0060–0065 (observer/simulador), ADR-0066 (política
  adaptativa). G1 es el primer experimento fuera de la familia Qwen.
- **Decisión:** extender el instrumentador (registry, adapter, CLI) con una
  rama `model_type == "gemma3n"` y trazar Gemma 3n E2B-it para caracterizar
  su anatomía de acceso — en particular la familia PLE (Per-Layer
  Embeddings), que es la respuesta arquitectónica de Google al problema de
  residencia que la tesis ataca en runtime.

## Contexto y justificación

El análisis MoE (Fases 0.6–1, ADR-0063/0065/0066) cerró el espacio de
políticas predictivas con 5 NO-GOs: el routing de expertos es muestreo de
alta entropía y la única señal es frecuencia estática. Gemma 3n resuelve el
mismo problema (inferencia bajo presupuesto de memoria) por el lado opuesto:
**no hay decisión dinámica en runtime — la arquitectura fija qué es frío**.
G1 mide esa decisión con nuestra metodología (traza LOGICAL_ACCESS →
anatomía → simulación a budgets de teléfono), que es agnóstica de
arquitectura. La predicción falsable: el perfil de acceso de 3n es
secuencial/determinístico (streaming estructural) en contraste con el
estocástico del MoE.

Hallazgos de exploración que fijan el diseño (transformers 5.17.0 local):

1. **PLE es un único módulo** (`model.language_model.embed_tokens_per_layer`):
   un nn.Embedding (262144 × 256×35) cuyo forward corre una vez por paso y
   produce las 35 embeddings por capa; las capas consumen su slice en orden
   L0→L34. La instrumentación hace fan-out a `L{i}.ple` con tamaño de FILA
   (n_tokens × 256 × dtype_bytes), no de tabla — mismo criterio que `embed`.
2. **Activation sparsity en FFN** (`_gaussian_topk`, ReLU con cutoff
   gaussiano por token según `activation_sparsity_pattern`): en eager
   PyTorch el matmul lee todos los pesos → NO es esparcidad de acceso; es
   "esparcidad potencial" que se reporta como métrica (candidato G1.5), no
   se instrumenta.
3. **Extras siempre calientes por capa**: laurel, altup, per_layer_input_gate,
   per_layer_projection → familia `L{i}.aug` (un bloque por capa, suma de
   esos módulos) para que el % de pesos tocados por token sea honesto.
4. **Atención híbrida 5:1** (cada 5ª capa full, resto sliding): el mapa
   full/swa de `kv_model_layers` ya lo soporta.
5. **KV compartida entre capas** (`shared_kv_states`): los eventos KV
   derivados sobrestiman para capas que comparten — caveat documentado en
   el análisis (G1 es sobre pesos; la KV es eje separado).

## Decisiones técnicas

- **Granularidad PLE**: bloque `L{i}.ple` = tabla completa de esa capa
  (262144×256×dtype) en el registry (ground truth de tamaños); eventos a
  tamaño de fila. El simulador trabaja a granularidad de bloque: PLE entra
  al pin base (se toca cada token) — correcto, porque Google misma documenta
  PLE como cacheable en almacenamiento local, no como residente obligatorio.
- **`L{i}.aug`**: un bloque por capa sumando laurel+altup+gates PLE
  (norms/extras menores quedan fuera — tamaño despreciable, documentado).
- **Vision/audio towers fuera del registry en G1** (corrida texto-only;
  no emiten eventos). Quedan como trabajo futuro si se trazan entradas
  multimodales.
- **E2B-it, no E4B**: E4B bf16 (~16 GB) no cabe holgado en la máquina de
  16 GB; E2B (~10 GB) tiene idéntica estructura PLE para la pregunta G1.
- **Bucle de decode**: si el loop manual (prefill+greedy con Cache API)
  se resiste a la híbrida sliding/full de transformers 5.x, fallback
  documentado a `model.generate()` — los hooks igual disparan por forward;
  se preserva la fidelidad de pesos (el objetivo de G1), no la de KV
  derivados.
- **checks V2a** (fórmula de bytes esperados por paso, Qwen-specific):
  guard que la reporta n/a para gemma-3n en vez de fallar.

## Qué G1 NO es (alcance)

- No instrumenta columnas FFN (activation sparsity como acceso) — G1.5.
- No cuantiza PLE ni hace MatFormer slicing — G2/G3.
- No corre E1-A (asume estructura Qwen), no multimodal, no E4B.

## Consecuencias

- `COMPONENTS` += PLE, AUG (events.py); registry/adapter/cli/checks con rama
  gemma3n (~+110 LOC → ~2,310/2,500 del cap R5, ADR-0064); tests con
  fixtures de nombres/config sintéticos (sin descargar el modelo).
- Entregable: `ai-notes/findings/g1-gemma3n-anatomia.md` con A1 anatomía por
  familia, A2 secuencialidad, A3 cola fría, A4 simulación a budgets
  teléfono, A5 tabla contraste MoE↔3n — predicciones marcadas antes de
  medir.
- **Riesgo de acceso**: google/gemma-3n-E2B-it es gated en HF; requiere
  licencia aceptada y autenticación del dueño. Sin eso, el código y los
  tests quedan listos pero la corrida espera.

## Actualización 2026-10-14 — Resolución del riesgo de acceso

El dueño eligió el **mirror comunitario `unsloth/gemma-3n-E2B-it`**
(ungated) sobre autenticarse en HF. Justificación:

- Mismos **safetensors bf16** que el checkpoint oficial → los tamaños de
  traza y bytes transferidos son directamente comparables con lo que daría
  el repo de Google (no es un re-quantizado tipo MLX 8-bit, que rompería
  la comparabilidad).
- La anatomía de acceso (la pregunta científica de G1) no depende del
  origen de los pesos: mismos shapes, mismos módulos.
- Caveat registrado: confianza en tercero (unsloth) para integridad de
  pesos; irrelevante para medir patrones de acceso, relevante si los
  pesos se usaran para evaluación de calidad de generación (no es el
  caso en G1).

## Actualización 2026-10-15 — Decisiones de instrumentación validadas contra
el checkpoint real

1. **Carga text-only**: `Gemma3nForConditionalGeneration` exige `timm`
   (vision tower) al construirse. Se usa `Gemma3nForCausalLM`: carga los
   pesos de lenguaje del checkpoint multimodal (towers = UNEXPECTED,
   0 MISSING) y expone los módulos como `model.*` (sin el segmento
   `language_model.`). El mapping de nombres acepta ambos prefijos.
2. **Owner único de `L{i}.aug` = `laurel`** (mismo patrón que
   `mlp` → `L{i}.ffn`): `altup` se invoca vía métodos `.predict()` /
   `.correct()` (sin forward hook), y `per_layer_input_gate` /
   `per_layer_projection` son hermanos que disparan hook propio. Si los
   cuatro emitieran el bloque, el conteo saldría 3× inflado (bug real
   detectado por V1: desviación 3.99% constante = 190MB/paso).
3. **Fórmula `_aug_bytes` validada contra parámetros reales** (exacta):
   laurel = 2·h·rank + h (norm); altup = h·n + n³ + n² + 2h
   (modality_router + prediction_coefs[n×n²] + correction_coefs[n×n] +
   router_norm + output_scale); gates = 2·h·ple_dim.
4. **`ple.proj` en la fórmula V1**: la proyección global PLE es un
   Linear denso que se lee entero (31.5MB/paso) — se suma una vez por
   paso en `expected_bytes_per_step`, y el conteo de prefill pasa a
   `2 + 4·n_layers + n_layers + 1`.
5. **Dato científico inesperado**: el checkpoint `-it` tiene
   `activation_sparsity_pattern` con **0 capas sparse** — la activation
   sparsity del FFN viene desactivada de fábrica en E2B-it. El
   experimento G1.5 (columnas FFN) queda sin sujeto en este checkpoint.
