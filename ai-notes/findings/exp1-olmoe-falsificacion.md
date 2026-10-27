# Exp1 — Falsación MoE #2: OLMoE-1B-7B-0924-Instruct

**Fecha:** 2026-10-21 · **Modelo:** `allenai/OLMoE-1B-7B-0924-Instruct` (ungated, bf16 13.0GB) · **ADR:** ADR-0068 · **Trazas:** `9951188c` / `4b850301` / `71d975d2` (ON ×3, p0, greedy, 64 tokens) + `42a93188` (OFF, V3) · **Máquina:** M2 16GB (swap masivo: ~1.5h por corrida, tokps ON 0.01)

## Por qué este modelo

Todas las conclusiones MoE previas (valle de spill, pin por frecuencia top-90%, 5 NO-GOs de prefetch) venían de UN modelo: Qwen1.5-MoE-A2.7B (60 expertos, top-4). OLMoE maximiza la distancia arquitectónica: 16 capas, **64 expertos, top-8**, sin shared_expert, sin GQA (16/16 kv), expertos fusionados (`OlmoeExperts`), vocab 50k, emb/lm_head no atados. Si las conclusiones son ley del MoE, sobreviven aquí.

**Predicciones marcadas antes de medir (del plan aprobado):**
- **P1:** demanda ≈ 2.3–2.5GB/token (17–18% del WS de 13.8GB)
- **P2:** el valle de spill EXISTE (hlwss-adaptive > lru en [0.85, 0.95]×WS, win ≥15% en el óptimo); más somero que Qwen (12.5% activo vs 6.7%)
- **P3:** sin predictibilidad cross-token (Jaccard ≈ azar ≈ 0.125)
- **P4:** routing concentrado por frecuencia (top-k pequeño cubre ~90% de accesos)

## Verificación de trazas

| Check | Resultado |
|---|---|
| V1 (bytes/paso decode, fórmula MoE nueva) | **OK criterion=0.0000** (63/63 pasos exactos) |
| V1 prefill (conteo MoE per-posición) | **OK** (tras fix: fused emite topk×prompt_len filas/capa) |
| V2 (router: emitido == topk(logits)) | **OK 1008 pasos** (16 capas × 63 tokens) |
| V4 (reproducibilidad, 3 corridas) | **OK criterion=1.0** |
| V3 (overhead) | **OK criterion=1.0** (ON 6873s vs OFF 5841s, ambos 0.01 tokps: el swap domina el wall-clock; por protocolo, no resuelve efectos <20% — la conclusión de overhead no detectable queda heredada de G1, ratio 1.018 en condiciones limpias) |

**Bug real encontrado en V1 prefill (fix en `checks.py`):** el path fusionado OLMoE ejecuta los expertos **por cada posición del prompt** en prefill (34×8=272 filas EXPERT/capa), no 1 vez por capa como asumía el conteo anterior (que Qwen1.5 pasaba por emitir 1 vez). El decode (criterio principal) ya era exacto: 0.0000. Fix: conteo por capa con `n_ex ∈ {topk, topk×prompt_len}`, +1 test sintético (`test_v1_moe_prefill_fusionado_por_posicion_pasa`). 75/75 tests verdes.

## A1' — Anatomía de la demanda

- **Demanda medida: 2362.2MB/token — P1 ✓ CONFIRMADA** (predicción 2300–2500MB; 17.1% del WS de 13.8GB).
- Familias: expertos 74.7%, attn 24.9%, kv 0.4% (contexto corto). lm_head 206MB/token (untied).
- Sin bloques "escondidos" tipo ple.proj de gemma3n: la fórmula MoE nueva cierra exacta sin términos extra.

## A2' — Secuencialidad

Primer-toque por paso: L0→L1→…→L15 **estricto (15/15 transiciones)**. Igual que Qwen y gemma3n: la cadena forward impone orden total.

## A3' — Cola fría

128 toques EXPERT/token = 8 por capa × 16 capas (constante, max=128) → **56/64 expertos fríos por capa por token** (87.5%). La sparsity de bloques es estructural: topk/num_experts = 12.5%.

## A4' — Simulación de políticas (budgets 8–14GB)

WS único tocado: **11.86GB** (fit a partir de 12GB). Totales transferidos (64 tokens, corrida 9951188c):

| Budget | lru | hlwss | hlwss-adaptive | oracle |
|---|---|---|---|---|
| 8 GB | 22.17 GB | **18.69 GB (−15.7%)** | 18.74 GB (−15.5%) | 14.44 GB (−34.9%) |
| 10 GB | 15.33 GB | 14.15 GB (−7.7%) | 14.15 GB (−7.7%) | 11.98 GB (−21.8%) |
| 11 GB | 13.26 GB | 12.91 GB (−2.6%) | 12.91 GB (−2.6%) | 11.86 GB (−10.5%) |
| 12 GB | 11.86 GB (fit) | 12.63 GB (+6.5% peor) | 11.86 GB (fit, degrada a lru ✓) | 11.86 GB |

**P2 ✓ CONFIRMADA.** Valle de spill en [8, 11]GB, win máx **−15.7% @8GB** (predicción: ≥15%, más somero que Qwen — cumple en ambos). La adaptativa detecta fit a 12GB y degrada a LRU (el pin estático paga cold-load y empeora +6.5%: el régimen fit es overhead, como diseñado en ADR-0066).

**Mecanismo revelador:** el win se da **aunque P4 falle** (routing plano). Con 87.5% de bloques fríos por estructura (topk 8/64), el valle viene de la **sparsity de activación**, no de la concentración de frecuencia. El pin por frecuencia aporta poco (cobertura efectiva ~51% con top-8 bloques/capa); el grueso del ahorro lo hace el SLRU sobre el stream frío.

## P3 — Afinidad cross-token (Jaccard entre tokens consecutivos, por capa)

**P3 ✗ FALSIFICADA.** Jaccar medio global 0.296; por capa 0.18–0.44 (media ~0.32) vs azar 0.125 → **2.4× sobre el azar** (~4 de 8 expertos compartidos entre tokens consecutivos). En Qwen el Jaccard era ≈ azar ( ese NO-GO de prefetch no generaliza). Hay **señal de prefetch real en OLMoE**: la selección de expertos es "pegajosa" entre tokens consecutivos.

## P4 — Concentración de frecuencia del routing

**P4 ✗ FALSIFICADA.** Cobertura acumulada por frecuencia (bloques EXPERT, por capa): top-8 cubre solo **41–63%** de los accesos (media ~51%); top-1 apenas 52–63 de 504 toques (~11%). Distribución casi plana: 813/1024 bloques tocados en 63 tokens. El supuesto "top-k pequeño cubre 90%" (cierto en Qwen) **no es ley del MoE**.

## A5' — Tabla comparativa 3 modelos

| | Qwen1.5-MoE (int4 sim) | gemma3n-E2B | OLMoE-1B-7B |
|---|---|---|---|
| Expertos / topk | 60 / 4 (6.7% activo) | denso | 64 / 8 (12.5% activo) |
| WS único | 9.32 GB | 10.09 GB | 11.86 GB |
| Demanda/token | 3.30 GB (29.6%) | 4.80 GB (acantilado) | **2.36 GB (17.1%)** |
| Régimen sim | valle [7.5, 8.6]GB | acantilado binario | **valle [8, 11]GB** |
| Win máx política | −25.1% @7.5GB | (oracle −78%, no online) | **−15.7% @8GB** |
| P3 Jaccard | ≈ azar | n/a | **0.30 (2.4× azar)** |
| P4 top-k frec. | ~90% con top pequeño | n/a | **~51% con top-8/capa** |
| Eje de sparsity | bloques (expertos) | filas (PLE, invisible a bloques) | bloques (expertos) |

## Veredicto de falsación

**Falsación MIXTA — la tesis sobrevive, dos mecanismos mueren:**

1. **Lo que generaliza (ley del MoE):** la sparsity de bloques por activación topk crea un valle de spill real donde una política model-aware supera al LRU (P1, P2, A2', A3'). La tesis del proyecto queda **reforzada**: el valle existe en 2/2 MoE de arquitecturas opuestas, con magnitud predicha (más activo → valle más somero: 6.7%→−25%, 12.5%→−15.7%).
2. **Lo que era artefacto de Qwen (muere):** P3 (sin predictibilidad cross-token) y P4 (routing concentrado en frecuencia). En OLMoE hay señal de prefetch real y el routing es plano. Consecuencias:
   - El **pin por frecuencia top-90%** (diseño hlwss) está sobrejustado a Qwen; en OLMoE el ahorro viene del SLRU frío, no del pin. Recomendación: el pin debe ser *shrink-to-fit con cobertura real medida*, no asumir concentración.
   - El NO-GO "prefetch imposible" **cae para OLMoE**: Jaccard 0.30 hace viable prefetch de ~4/8 expertos del token siguiente (Fase 2 debe re-evaluar el NO-GO por modelo, no universal).

## Verificación

- V1 0.0000 / V2 1008 OK / V4 1.0 sobre trazas filtradas reales (symlinks en /tmp/exp1-check).
- 75/75 tests (incl. 4 MoE: olmoe-style, shared-expert regresión, missing-experts, per-posición).
- CSV sweep: /tmp/exp1-sim.csv.
