# Simulator Fase 0.7 — replay de políticas de residencia sobre trazas MoE reales

**Fecha:** 2026-10-06 · **ADR:** 0064 (cap R5), 0065 (diseño) · **Código:** `vendor/hlwss/hlwss/simulate.py` + `hlwss simulate` · **Tests:** 10 nuevos (54 total verdes)

## Setup
- Trazas: 3 corridas Qwen1.5-MoE-A2.7B (bf16, Fase 0.6: `20e58cc0`, `a581aa8c`, `e820370d`), 64 tokens generados, stream de 25,600–37,888 eventos LOGICAL_ACCESS.
- Política candidata **hlwss**: pin (no-expertos tocados + expertos top-frecuencia cubriendo 90% de accesos = 7.38GB) + SLRU 80/20 sobre el resto. Rieles: **lru** (piso) y **oracle** (Belady, techo).
- Tamaños desde `registry.blocks` del metadata. El pin paga su carga en frío (one-time); pinned > budget → `infeasible`.

## Resultado (idéntico en las 3 corridas — mismos prompts, determinismo confirmado)

| budget | lru GB | hlwss GB | oracle GB | hlwss vs lru | gap al oráculo |
|--------|--------|----------|-----------|--------------|----------------|
| 6 GB   | 18.371 | 11.139 ⚠️ infeasible | 12.493 | −39.4% | — (usa 7.38GB pin) |
| 8 GB   | 11.360 | **10.234** | 9.324 | **−9.9%** | cierra **55%** del gap lru→oracle |
| 10 GB  | 9.324  | 9.551    | 9.324 | +2.4% (peor) | 0% (todo cabe) |
| 12 GB  | 9.324  | 9.350    | 9.324 | +0.3% (peor) | 0% |

## Lectura — la tesis se sostiene, con precisión de régimen

1. **La política model-aware gana EXACTAMENTE donde la tesis lo predice: el
   régimen SPILL.** A 8GB (presión real de memoria) hlwss transfiere 9.9%
   menos que el LRU del OS y recorre 55% del camino hacia el óptimo teórico.
2. **En régimen FIT (10GB+) cualquier política es costo puro**: todo el
   working set único (9.324GB) cabe; hlwss pierde un 2.4% por el cold segment
   del SLRU (thrashing de 185 re-cargas evitables). Negativo honesto y
   esperado — no es un bug, es la frontera del modelo.
3. **El piso de viabilidad del pin es 7.38GB**: bajo eso la política es
   infeasible (marcado, no crashea). El régimen útil de la tesis es
   budget ∈ [7.4, 9.5]GB para este modelo.
4. **El oráculo enseña dónde está el headroom restante**: a 8GB, Belady logra
   9.324GB (= cada bloque exactamente una vez). La diferencia con hlwss
   (0.91GB) son expertos fríos que el SLRU no predice — palanca para Fase 1
   (prefetch por frecuencia de ventana, o hot-set dinámico).

## Artefacto contable detectado y corregido (antes de publicar)

La primera corrida mostró hlwss "ganándole al oráculo" (−70%): artefacto —
el pin se contaba residente gratuito (sin carga) e incluía bloques phantom
(`L{i}.ffn` denso que el registry construye pero un MoE real no tiene).
Fix: pin intersectado con bloques tocados + `transferred += pin_bytes` al t=0.
Con contabilidad honesta el resultado flota pero la dirección se mantiene
(−9.9% en régimen spill). **Lección: en simuladores de memoria, el costo de
carga inicial de lo "pre-residente" es el primer lugar donde miente el modelo.**

## Caveats declarados (ADR-0065)

- Frecuencias del pin medidas del mismo trace replayado (sesgo a favor de
  hlwss, acotado; en producción vendrían de telemetría histórica).
- Sweep de umbrales (hot-set %, split SLRU, budgets intermedios 7–9GB) es la
  fase de análisis siguiente — el crossover exacto lru↔hlwss vive ahí.
- No emite PHYSICAL_TRANSFER/PREFETCH/EVICT (reservado para engine Fase 1).

## Sweep fino del valle SPILL (2026-10-08, run 20e58cc0, pasos de 0.25GB)

| budget GiB | lru GB | hlwss GB | oracle GB | hlwss vs lru | feasible |
|-----------|--------|----------|-----------|--------------|----------|
| 7.25 | 14.798 | 11.139 | 9.854 | +24.7% | ⚠️ infeasible (pin 7.38 > budget) |
| **7.50** | 14.395 | **10.780** | 9.487 | **+25.1%** ✅ | feasible (mínimo) |
| 7.75 | 11.786 | 10.311 | 9.324 | +12.5% | ✓ |
| 8.00 | 11.360 | 10.234 | 9.324 | +9.9% | ✓ |
| 8.25 | 11.051 | 10.187 | 9.324 | +7.8% | ✓ |
| 8.50 | 10.595 | 10.178 | 9.324 | +3.9% | ✓ |
| 8.75 | 9.576 | 10.161 | 9.324 | **−6.1%** | ✓ (crossover ~8.6GB) |
| 9.00 | 9.380 | 10.140 | 9.324 | −8.1% | ✓ |
| 9.25 | 9.329 | 10.131 | 9.324 | −8.6% | ✓ (máxima pérdida) |
| 9.50 | 9.324 | 10.072 | 9.324 | −8.0% | ✓ |

CSV: `simulator-fase07-sweep-fino.csv`. Tres lecturas:

1. **El win es de "orilla de acantilado", no de meseta:** hlwss gana su máximo
   (−25.1%) justo arriba de su piso de viabilidad (7.5GB) y la ventaja decae
   monótonamente con el budget, cruzando a negativo en ~8.6GB. Su curva es
   casi plana (10.78→10.07GB) porque el pin la domina; el LRU mejora rápido
   con más RAM y lo alcanza.
2. **La ventana útil de la tesis mide ~1.1GB de ancho** ([7.5, 8.6]GB para
   este modelo en int4) — pero es exactamente la ventana donde vive un
   dispositivo de 16GB corriendo un modelo de ~10.5GB + OS + KV. El régimen
   no es exótico: es el desktop/laptop real.
3. **El oráculo sigue ~1.3GB abajo de hlwss en el mejor punto** (9.49 vs
   10.78 a 7.5GB): headroom de otro −12% que la política ESTÁTICA no captura
   — necesita hot-set dinámico + prefetch = exactamente lo que el engine de
   Fase 1 aportaría sobre el simulador.

## Veredicto Fase 1 (con datos del valle)

El sweep NO mató la tesis: acotó dónde aplica (~1GB de ventana en la orilla
del spill) y cuánto queda en la mesa (el oráculo). Fase 1 tiene sentido
acotado: su valor marginal es (a) hot-set dinámico para entrar al valle
desde budgets menores (a 7.25GB la política actual ya no cabe — uno con
hot-set 80% sí cabría), y (b) prefetch para recorrer el último ~12% hacia el
techo. Sin esos dos componentes, el simulador ya extrajo casi todo lo que la
política estática da.

## Análisis del gap al oráculo (2026-10-08): go/no-go del prefetch

Pregunta: ¿los 1.3GiB que separan hlwss del oráculo a 7.5GB son predecibles
por historial? Método: traza 20e58cc0, stream de 8,832 toques de experto
(881 no-pinneados), políticas simuladas con el budget real.

**Resultados (los tres candidatos RECHAZADOS por los datos):**

1. **Prefetch por ventana de recencia: NO-GO.** Distancia de repetición de
   expertos no-pinneados: p50=115 toques, p90=348 (≫ capacidad de 29 slots).
   Window-recall: K=32 → 22.7%, K=128 → 25.2%, K=512 → 46.4%. La cola fría
   no es predecible por recencia — son cambios de tema, no patrones cortos.
2. **2Q (admission por re-touch): NO-GO.** 3,271MiB transferidos vs LRU
   2,986MiB con la misma capacidad — el filtro de admisión retrasa justo lo
   que aquí nunca vuelve a tiempo.
3. **"El pin es demasiado grande" (hlwss-lite, pin solo no-experto): NO-GO.**
   11.63GiB vs 10.78GiB del pin completo a 7.5GB — y el sweep de cobertura
   confirma la inversa: top-50% = 11.40, top-70% = 11.19, **top-90% = 10.78
   (mejor)**. Los expertos calientes pagados por adelantado sí se amortizan.

**Decomposición final del gap a 7.5GiB:** el oráculo ≈ piso teórico (cada
bloque exactamente una vez ≈ 9.49GiB, el working set único). hlwss retransfiere
1.46GiB de repeticiones de la cola no-pinneada (distancias p50=115 con 29
slots ⇒ miss estructural, no de política). Ese re-transfer es el único
residuo y **no es capturable con información de historial**.

**Veredicto Fase 1 actualizado (con este análisis):** el prefetch de
expertos queda desprioritizado (NO-GO con evidencia). El valor restante de
Fase 1 se concentra en dos piezas que el simulador estático no puede dar:
- **Detección de régimen** (soltar el pin en FIT: vale +8.6% en 8.75GB+).
- **Hot-set que encoge con el budget disponible** (cuando KV/contexto
  consumen RAM, el pin fijo de 7.38GB se vuelve infeasible — a 6GB la
  política actual ya no cabe; la versión dinámica recorta la cobertura
  hot-set en vez de caerse).
- (Lever de formato ya validado aparte: expertos int4 = 2.6× más expertos
  por GB de pin, ADR-0063.)
Caveat: un solo run de 64 tokens; generaciones más largas podrían mostrar
localidad de tema más fuerte. Re-evaluar si Fase 1 corre con max_tokens ≥512.

## Post-script: prefetch por AFINIDAD de routing (2026-10-13) — 2 NO-GOs más

La pregunta pendiente era si existía una señal distinta a la recencia. Se
probaron las dos únicas fuentes de contexto observables en runtime:

4. **Afinidad cross-token (grafo de co-ocurrencia): NO-GO.** Cada token activa
   en promedio 48.2 de 60 expertos (unión sobre 24 capas, top-4 por capa) —
   el pool se cubre ~80% por token. Jaccard(S_t, S_t+1) = 0.679 real vs
   **0.710 azar**: el routing consecutivo es menos pegajoso que el azar.
   El recall@29 sobre la cola fría (4 toques en la 2a mitad) es ruido, no
   evidencia. El grafo de co-ocurrencia no aporta nada sobre la frecuencia.
5. **Afinidad cross-capa dentro del mismo token (la única ventana temporal
   real — capas 0..L-1 ya resolvieron su routing cuando L computa): NO-GO.**
   Jaccard(S^L, ya vistos en el token) = 0.063 vs 0.060 azar (n=1,449).
   recall@4: co-ocurrencia 13.4% ≈ frecuencia global 14.6% > azar-masa 10.2% —
   la co-ocurrencia no supera al prior de frecuencia, y la frecuencia solo
   ligeramente al azar: **el routing por capa es muestreo casi uniforme con
   una inclinación débil por frecuencia.**

**Lectura estructural (este es el hallazgo del análisis):** el routing de
expertos en este modelo se comporta como muestreo de alta entropía — no
hay localidad temporal (recencia), ni estructural (co-ocurrencia), ni
trans-layer que un prefetch pueda explotar. La única señal es la frecuencia,
que es ESTÁTICA y ya la usa el pin. Esto explica por qué el pin top-90% es
óptimo entre políticas con historial, y cierra el espacio de prefetch
basado en telemetría de accesos. Única puerta teórica restante: un predictor
entrenado sobre el hidden-state (distillation del router), que es un
proyecto de ML con telemetría extra — fuera de alcance de Fase 1 y con la
entropía observada como techo de cuánto podría dar.

## Fase 1 preflight: política hlwss-adaptive (2026-10-13, ADR-0066)

Nueva política con los dos componentes mecánicos que sobrevivieron al análisis
del gap: detección de régimen FIT + pin shrink-to-fit (cobertura target 0.9
acotada por `pin_cap = β × budget`, β=0.9). Sweep sobre 20e58cc0, políticas
lru / hlwss / hlwss-adaptive / oracle:

| budget GiB | lru GB | hlwss (static) | **hlwss-adaptive** | régimen | cov | oracle |
|---|---|---|---|---|---|---|
| 6.00 | 18.371 | 11.139 ⚠️ infeasible | **21.276 (−15.8%)** ⚠️ | spill | 0.37 | 12.493 |
| 7.00 | 15.651 | 11.139 ⚠️ infeasible | **11.724 (−25.1%)** | spill | 0.75 | 10.358 |
| 7.25 | 14.798 | 11.139 ⚠️ infeasible | **11.160 (−24.6%)** | spill | 0.79 | 9.854 |
| 7.50 | 14.395 | 10.780 (−25.1%) | 10.870 (−24.5%) | spill | 0.83 | 9.487 |
| 8.00 | 11.360 | 10.234 (−9.9%) | 10.345 (−8.9%) | spill | 0.88 | 9.324 |
| 8.50 | 10.595 | 10.178 (−3.9%) | 10.178 (−3.9%) | spill | 0.90 | 9.324 |
| 8.75 | 9.576 | 10.161 (+6.1% peor) | 10.161 (+6.1% peor) | spill | 0.90 | 9.324 |
| 9.00 | 9.380 | 10.140 (+8.1% peor) | 10.140 (+8.1% peor) | spill | 0.90 | 9.324 |
| 9.50 | 9.324 | 10.072 (+8.0% peor) | **9.324 (0.0%)** | **fit** | — | 9.324 |

CSV: `simulator-fase1-sweep.csv`. Verificación del plan:

- **(b) shrink-to-fit: CONFIRMADO con matiz.** Donde la estática no corría
  (7–7.25GB), la adaptativa gana ~−25% vs LRU. Pero a 6GB con β=0.9 es
  **−15.8% PEOR que LRU** (negativo honesto): con cobertura recortada a 37%
  y SLRU de solo 0.6GB, el pin parcial no amortiza y la cola thrashea.
- **(a) régimen FIT: CONFIRMADO solo en FIT real.** A 9.5GB (WS único
  9.324 ≤ budget) degrada a LRU y elimina la pérdida del −8.0%. En 8.75–9.25GB
  sigue siendo spill (el WS NO cabe) y la pérdida −6..−8.1% **no es
  recuperable por detección de régimen** — es spill real donde LRU le gana al
  pin. Corrección a la predicción del plan: el boundary del régimen no es
  ~8.6GB (crossover), es 9.32GB (WS único). El costo del pin en near-FIT es
  real, no overhead evitable.
- **(c) valle preservado:** a 8.5GB+ paridad exacta con la estática (el cap
  β=0.9 ya no limita); a 7.5–8.0GB el cap recorta el pin ~1pp de win
  (10.870 vs 10.780; 10.345 vs 10.234) — artefacto del β elegido, no del
  diseño.

### Sensibilidad de β (probe, no en código — decisión diferida)

| budget | β=0.8 | β=0.9 | β=1.0 |
|---|---|---|---|
| 6.0 | −56.0% (pin=base sola) | −15.8% | **+1.5%** |
| 7.0 | +22.2% | **+25.1%** | +21.5% |
| 7.25 | +21.6% | **+24.6%** | +22.1% |
| 7.5 | +22.0% | +24.5% | **+25.1%** (= static) |
| 8.0 | +7.6% | +8.9% | **+9.9%** (= static) |
| 8.5 | +3.0% | +3.9% | +3.9% |

(% vs LRU, hlwss-adaptive) Tres lecturas:
1. **β=1.0 es la opción no-regret:** nunca pierde vs LRU en ningún budget,
   replica EXACTAMENTE a la estática donde cabe (7.5–8.5), y extiende
   viabilidad a 6GB (+1.5%).
2. **β=0.9 gana +3-4pp en 7–7.25GB** (el sliver SLRU de ~0.7GB sí captura
   reutilización) pero tiene un acantilado a 6GB (−15.8%): cuando el
   remainder SLRU cae por debajo del umbral donde captura reuse, es mejor
   dárselo todo al pin (a 6GB el sliver de 0.6GB no retenía nada y la
   cobertura extra 37→68% de β=1.0 convirtió misses en hits a costo cero).
3. **β óptimo es función del budget** (≈1.0 ≤6.5GB, ≈0.9 en 7–7.5GB,
   irrelevante ≥8.5GB): la regla mecánica "sliver solo si puede retener la
   cola reutilizable" es la versión práctica de la asignación dinámica que el
   oráculo hace con mirada al futuro. Elegir β default = siguiente análisis
   ( candidatos: β=1.0 fijo no-regret, o regla umbral sobre remainder).

## Comando reproducible
```bash
cd vendor/hlwss && .venv/bin/python -m hlwss.cli simulate \
  --traces traces --policy all --ram-budget-gb 6,8,10,12 --csv out.csv
```

## Gráficas
- `simulator-fase07-curvas.png` — bytes transferidos × budget, 3 políticas,
  regiones INFEASIBLE/SPILL/FIT sombreadas, anotación del punto de tesis.
- `denso-vs-moe-bytes-por-token.png` — pesos leídos por token (media decode,
  dedupe por token×block): denso ≈100% del modelo vs MoE 29.6% (expertos
  solo 0.41GB = 6.2% del pool). El espacio vacío de la barra MoE ES la tesis.
- `e1a-denso-vs-moe-agreement.png` — E1-A comparativo: acuerdo argmax por
  capa (fracción de profundidad). El MoE estabiliza MÁS TARDE que el denso
  (curva roja bajo la media azul entre 60–95% de profundidad): las capas
  intermedias del MoE revierten el argmax con más frecuencia. Consecuencia:
  early exit sería aún más débil en MoE que en denso (consistente con el
  negativo honesto de E1-A); la palanca correcta para MoE es residencia de
  pesos, no ahorro de capas. Caveats: MoE en int4 (la config que corre en
  16GB), 1 corrida × 64 tokens vs 15 corridas densas — direccional, no
  concluyente. Traza: `e3823cb9…` (run E1-A sobre Qwen1.5-MoE int4).
