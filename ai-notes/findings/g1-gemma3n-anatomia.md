# G1 — Anatomía del patrón de acceso de Gemma 3n E2B (PLE)

**Fecha:** 2026-10-15 · **ADR:** 0067 · **Modelo:** `unsloth/gemma-3n-E2B-it`
(text-only vía `Gemma3nForCausalLM`, 4.44B params, bf16)
**Máquina:** Apple M2, 16GB · **Protocolo:** `prompts/validation.json` p0
(29 tokens), greedy, 64 tokens generados, 3 repeticiones + 2 corridas OFF

**Pregunta científica:** ¿dónde puso Google la esparcidad que el MoE pone en
el routing? Predicción a falsar: el perfil 3n es lo opuesto al MoE en las
dimensiones que medimos (secuencial/determinístico vs estocástico;
esparcidad a granularidad de fila vs de bloque).

---

## Verificación de traza

| Check | Resultado | Detalle |
|---|---|---|
| V1 (bytes decode ±10%) | **OK, criterion=0.0** | match exacto traza vs fórmula (63 pasos) |
| V1 prefill (conteo) | **OK 153/153** | 2 + 4·30 + 30 + 1 (ple.proj) |
| V4 (reproducibilidad) | **OK** | 3 corridas idénticas (greedy determinista) |
| V3 (overhead hooks) | ver §V3 | tokps con varianza 3.7× por carga de máquina |

Bug real encontrado y corregido en el camino: `altup` se invoca vía
`.predict()`/`.correct()` (métodos, sin forward hook) y sus hermanos
`laurel`/`per_layer_input_gate`/`per_layer_projection` disparan hook propio;
con los 4 emitiendo el bloque AUG completo el conteo salía 3× inflado
(V1 lo detectó: desviación constante de 3.99% = 190MB/paso). Fix: owner
único (`laurel`), mismo patrón que `mlp` → `L{i}.ffn`. Fórmula `_aug_bytes`
revalidada contra parámetros reales del checkpoint (exacta).

## Config real vs asunciones

- 30 capas (no 35), `intermediate_size` lista MatFormer (todo 8192),
  `num_kv_shared_layers=10` (L20–L29 sin k_proj/v_proj),
  head_dim=256, 6 capas full attention ((i+1)%5==0 → L4,9,…,29), resto SWA 512.
- **`activation_sparsity_pattern`: 0 capas sparse en el checkpoint `-it`.**
  La activation sparsity del FFN viene desactivada de fábrica → G1.5
  (columnas FFN) queda sin sujeto en este checkpoint. Hallazgo en sí mismo:
  el `-it` post-entrenamiento no usa la esparcidad de activación.

---

## A1 — Anatomía: bytes/paso por familia (decode, idéntico en las 3 corridas)

| Familia | bytes/paso | % demanda | pesos en registry | % pesos |
|---|---:|---:|---:|---:|
| FFN | 3.0199 GB | 63.0% | 3.0199 GB | 34.2% |
| LM_HEAD | 1.0748 GB | 22.4% | 0 (tied→embed) | — |
| ATTN | 0.5872 GB | 12.2% | 0.5872 GB | 6.7% |
| AUG | 0.0795 GB | 1.7% | 0.0795 GB | 0.9% |
| PLE | 0.0315 GB | 0.66% | **4.0579 GB** | **46.0%** |
| EMBED | 4.1 KB | ~0% | 1.0748 GB | 12.2% |
| KV (emitido) | 3.75 MB | 0.08% | derivado | — |
| **TOTAL** | **4.797 GB/token** | 100% | 8.82 GB | 100% |

**El truco PLE, cuantificado (y su corolario):**

- Las 30 tablas PLE son **46% de los pesos** (4.06GB: 262144 vocab × 256 dim
  × 2B × 30 capas) pero su demanda por token son **30 filas × 512B = 15.4KB**
  — el 0.0004% de su almacenamiento. Predicción A1 ("~18KB/token") acertada
  en orden de magnitud.
- Corolario inesperado: la proyección global `ple.proj` (Linear densa
  2048→30×256, 31.46MB) **se lee entera cada paso** y domina la familia:
  31.46MB de los 31.47MB PLE/paso. El PLE no es gratis: es barato en
  tablas, caro en la proyección densa que las sirve.
- La demanda real por token (4.797GB) es 54.4% del modelo — todo denso
  leído entero (FFN+ATTN+AUG+LM_HEAD+proj). La otra mitad del modelo
  (tablas PLE 4.06GB + embed 1.07GB) se toca a granularidad de fila.

## A2 — Secuencialidad

63/63 tokens con orden estrictamente monótono L0→L29 (ATTN+FFN por
timestamp), 0 violaciones. Predicción confirmada: el decode 3n es
perfectamente secuencial y determinista — sin routing estocástico.

## A3 — Cola fría a granularidad de bloque

**153/153 bloques tocados en cada paso de decode; cola fría = ∅.**
Predicción confirmada: a granularidad de bloque NO hay esparcidad ninguna.
La esparcidad estructural de 3n vive exclusivamente a granularidad de **fila
de tabla** (filas PLE, filas embed) — vs el MoE, donde vive a granularidad
de **bloque de pesos** (expertos).

## A4 — Simulación a budgets de teléfono (bloque a bloque, `--policy all`)

| Budget | LRU hit% / GB/token | hlwss pin | oracle hit% / GB/token | oracle vs LRU |
|---:|---|---|---|---|
| 2 GB | 0.0% / 10.33 | infeasible (pin 10.09GB) | 12.3% / 8.32 | −19.5% |
| 3 GB | 0.0% / 10.33 | infeasible | 17.5% / 7.31 | −29.3% |
| 4 GB | 0.0% / 10.33 | infeasible | 22.7% / 6.30 | −39.0% |
| 6 GB | 0.0% / 10.33 | infeasible | 47.3% / 4.28 | −58.5% |
| 8 GB | 0.0% / 10.33 | infeasible | 76.4% / 2.27 | −78.0% |

- **LRU: 0% de hits a TODO budget ≤8GB.** El WS único (8.82GB) no cabe
  incluso al 91% (8GB) → thrash total, ~10.33GB transferidos por token.
- **Pin estático: infeasible en todos los budgets** (no hay subconjunto de
  bloques que omitir — todo se toca siempre).
- **Ni el oracle con conocimiento perfecto baja de 2.27GB/token a 8GB.**
- Predicción ("acantilado binario, sin valle") confirmada con crudeza: el
  contraste con el valle MoE [7.5, 8.6]GB es total.

### El matiz de granularidad (crítico para interpretar A4)

El simulador transfiere **bloques enteros** ("nunca inferir tamaños de
eventos"). En la máquina real, el OS pagina a **4KB**: las filas PLE calientes
(~92 tokens únicos × 512B) cabrían en ~11MB de páginas. A granularidad de
página, el 46% PLE del modelo es casi gratis y el acantilado se suaviza.
Conclusión honesta: **el acantilado es artefacto de la granularidad de
residencia, no una ley del modelo** — lo que G1 demuestra es que 3n concentra
su esparcidad donde una política por bloques no puede verla, y donde el OS
(páginas) sí la ve gratis. La tesis "model-aware > OS LRU" tiene aquí su
caso más hostil: no hay señal de prefetch (A2+A3), y la señal de residencia
que queda (filas) ya la explota el hardware.

### Caveat de conteo en A4

`lm_head` (tied, size 0) se excluye de los bloques del simulador: su demanda
real (1.07GB/token) no entra en `bytes_transferred` (64 touches en
`n_unknown`). El total real es peor que el reportado; no cambia la forma
de la conclusión.

## A5 — Contraste MoE ↔ 3n (mismas métricas)

| Dimensión | Qwen1.5-MoE (int4) | Gemma 3n E2B (bf16) |
|---|---|---|
| Pesos | 9.32 GB (WS único) | 8.82 GB |
| Demanda/token | 3.30 GB (29.6% de pesos) | 4.80 GB (54.4%) |
| ¿Esparcidad a granularidad de bloque? | Sí: 48.2/60 expertos por token (6.2% del pool) | **No: 153/153 bloques** |
| Estructura temporal | Routing estocástico (Jaccard≈azar) | Secuencial estricto L0→L29 |
| Valle de spill | [7.5, 8.6]GB, −25.1% @7.5 | **No existe (acantilado)** |
| ¿Qué explota una política? | Frecuencia de expertos (pin top-90%) | Nada a granularidad de bloque |
| Dónde vive la esparcidad | Bloques de pesos (routing) | Filas de tabla (PLE/embed) |

La oposición predicha se confirma en cada fila: el MoE es esparso en
**espacio de pesos** (qué bloques), el 3n es esparso en **espacio de
vocabulario** (qué filas) — y solo la primera forma es accionable por una
política de residencia de pesos.

---

## Appendix

- **KV compartida (caveat cuantificado):** L20–L29 comparten KV de una capa
  previa del mismo tipo (`is_kv_shared_layer`). Los eventos KV derivados
  emiten 3.75MB/paso; descontando duplicados: **2.50MB/paso** (sobreestima
  33%). No afecta conclusiones (KV es 0.08% de la demanda).
- **Activation sparsity:** `activation_sparsity_pattern` con 0 capas sparse
  en el checkpoint `-it` (ver Config). G1.5 sin sujeto; para medirla haría
  falta el checkpoint base (pre-instruct) o activarla por config.
- **V3:** tokps con/sin hooks — 3 corridas ON idénticas dieron 0.91/2.46/3.38
  tokps (varianza 3.7× por carga de máquina en la M2). Medición limpia
  (nada más corriendo, par ON/OFF intercalado): ON 3.48 (`e2b2b1c9`) vs
  OFF 3.42 (`7c1d87aa`) → **ratio 1.018, overhead no detectable** (<2%).
  Con carga simultánea el ratio de una corrida aislada cae a 0.27–0.72.
  **Veredicto: el overhead de hooks es despreciable; la varianza de la
  máquina no lo es — V3 de una sola corrida es no concluyente, requiere
  pares intercalados o medianas.**
- **Carga:** `Gemma3nForCausalLM` carga solo los pesos de texto del
  checkpoint multimodal (towers = UNEXPECTED, 0 MISSING). Sin dependencia
  `timm`. 10.1GB en disco, ~8.9GB en RAM bf16 + KV.
- **Trazas:** `53c82d2b…`, `20a48384…`, `0f1af5df…` (ON, 17,664 eventos
  c/u); `6b9e9e34…`, `7c1d87aa…` (OFF). Simulación: `/tmp/g1-sim.csv`.

## Aprendizajes

1. **Las filas vs los bloques son el eje que separa MoE de 3n.** Toda la
   discusión de políticas de residencia cambia según dónde viva la
   esparcidad del modelo objetivo.
2. **Un forward hook por familia, no por módulo** — los métodos auxiliares
   (`altup.predict`) no disparan hooks y los hermanos que sí lo hacen
   duplican conteos si comparten bloque. Regla: un bloque, un owner, como
   con `mlp`/`shared_expert` en Qwen.
3. **V1 con criterion=0.0 es alcanzable** cuando la fórmula esperada se
   valida contra parámetros reales del checkpoint (no contra la doc).
4. **La varianza de tokps en M2 (~3.7×) mata cualquier V3 de una corrida
   aislada**; hay que reportar medianas o corridas intercaladas on/off.
5. **El checkpoint importa:** el `-it` de Gemma 3n trae la activation
   sparsity apagada — un experimento diseñado sobre ella (G1.5) muere en
   el paso 0 de verificación de config. Leer la config real antes de
   instrumentar vale por sesiones enteras.
