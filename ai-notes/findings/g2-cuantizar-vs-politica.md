# G2 — Cuantizar vs política: simulación de escenarios de cuantización sobre gemma-3n

**Fecha:** 2026-10-20 · **Traza:** `53c82d2b` (gemma-3n-E2B-it, 64 tokens, greedy, ON) · **ADR:** ADR-0068 · **Fase:** G2 (simulación bytes-only; la dimensión *calidad* de la cuantización es G2b, futura)

## Pregunta

Dueño de un dispositivo con poca RAM: "mi modelo no cabe: ¿cuantizo, o invierto en una política de residencia model-aware?" Simulamos escenarios de cuantización reescalando los tamaños de bloque por familia de componentes (`scale_blocks`, ADR-0068) y medimos dónde se mueve el acantilado de A4 y qué aporta cada política en cada escenario.

## Escenarios

| Scenario | Familias cuantizadas (int4 ≈ ×0.25) | Pesos bf16 | Pesos escalados | WS tocado sim |
|---|---|---|---|---|
| S0 baseline | — | 8.82 GB | 8.82 GB | 10.089 GB |
| S1 PLE int4 | PLE (37.5% del WS) | 8.82 GB | ~6.30 GB | 7.254 GB |
| S2 PLE+FFN int4 | PLE + FFN (65.4%) | 8.82 GB | ~3.90 GB | 5.145 GB |
| S3 todo int4 | todos los pesos (`weights:0.25`) | 8.82 GB | 2.21 GB | 3.928 GB |

Composición del WS único (traza 53c82d2b): PLE 4058MB (37.5%), FFN 3019.9MB (27.9%), KV 2013.3MB (18.6%, **no escalable** — derivado), EMBED 1074.8MB (9.9%), ATTN 587.2MB (5.4%), AUG 79.5MB (0.7%). Total registry 10.833GB; tocado por el stream 10.089GB.

Flags: `--scale-component PLE:0.25`, `--scale-component PLE:0.25 --scale-component FFN:0.25`, `--scale-component weights:0.25`. Budgets 2–11GB × políticas lru / hlwss / hlwss-adaptive / oracle. CSVs: `/tmp/g2-S0.csv`, `g2-PLE_0_25.csv`, `g2-S2.csv`, `g2-weights_0_25.csv`, + afinaciones `g2-*-fine.csv`.

## Resultado 1 — umbral fit (dónde muere el acantilado)

LRU `bytes_transferred` total (64 tokens) justo debajo/encima del umbral:

| Scenario | Último budget en acantilado | bytes LRU | Primer budget fit | bytes fit | Umbral fit |
|---|---|---|---|---|---|
| S0 bf16 | 10 GB | 645.7 GB | 11 GB | 10.09 GB (1×) | **11 GB** |
| S1 PLE:0.25 | 7 GB | 464.3 GB | 8 GB | 7.25 GB (1×) | **8 GB** |
| S2 PLE+FFN:0.25 | 5 GB | 329.3 GB | 6 GB | 5.14 GB (1×) | **6 GB** |
| S3 weights:0.25 | 3 GB | 251.4 GB | 4 GB | 3.93 GB (1×) | **4 GB** |

El acantilado es binario a granularidad de bloque (confirmación de G1/A4 en los 4 escenarios): por debajo del umbral LRU transfiere ~161MB/token (demanda completa, 0% hits); por encima, cada bloque se transfiere exactamente 1× y los 4 criterios convergen (98.4% hits, bytes = WS). No hay valle gradual en ningún escenario — en un modelo denso la política a granularidad de bloque no puede fabricar hits parciales.

**Cuantizar mueve el umbral:** S3 (int4 todo) lo baja de 11 GB a 4 GB (−7 GB, −64%). S1 (solo PLE, la familia grande y densa) baja 3 GB. S2 baja 5 GB.

## Resultado 2 — qué aporta la política (hlwss / hlwss-adaptive / oracle)

En los budgets por debajo del umbral fit:

| Scenario @ budget | lru | hlwss-adaptive | oracle | nota |
|---|---|---|---|---|
| S0 @ 8 GB | 645.7 GB | pin 10.09 GB → infeasible | 142.0 GB | hlwss "gana" solo excediendo el budget |
| S1 @ 6 GB | 464.3 GB | pin 7.25 GB → infeasible | 89.9 GB | idem |
| S2 @ 4 GB | 329.3 GB | pin 5.14 GB → infeasible | 80.0 GB | idem |
| S3 @ 3 GB | 251.4 GB | pin 3.93 GB → infeasible | 63.0 GB | idem |
| S3 @ 2 GB | 251.4 GB | pin 3.93 GB → infeasible | 125.5 GB | idem |

Lectura honesta: en el régimen spill de un modelo **denso**, el pin model-aware no cabe (el pin son todo el WS menos KV), así que su "victoria" de −98% bytes viene marcada `infeasible=True` — necesita más RAM de la disponible. El **oracle** (Belady) sí reduce significativamente (p.ej. S3 @3GB: 251→63 GB, −75%) porque conoce el futuro: muestra que *existe* estructura aprovechable (los toques KV por capa son repetibles), pero ninguna política online real la captura a granularidad de bloque en un stream denso.

Contraste con Qwen1.5-MoE (golden A4): ahí el pin cubría el 90% de accesos EXPERT con ~7.4GB y el valle de spill era real y feasible ([7.5, 8.6]GB, win −25.1%). La diferencia no es la política, es el **modelo**: el MoE tiene una familia (expertos) con baja fracción activa (6.7%) donde el pin es pequeño vs demanda; el denso no.

## Resultado 3 — la respuesta práctica (cuantizar vs política)

A budget fijo, comparación cruzada (bytes LRU totales; "fit" = transfiere 1× WS):

| Budget | S0 bf16 | S1 PLE:0.25 | S2 PLE+FFN:0.25 | S3 weights:0.25 |
|---|---|---|---|---|
| 2 GB | 645.7 GB | 464.3 GB | 329.3 GB | 251.4 GB (cliff) |
| 3 GB | 645.7 GB | 464.3 GB | 329.3 GB | 251.4 GB (cliff) |
| 4 GB | 645.7 GB | 464.3 GB | 329.3 GB | **3.93 GB fit** |
| 6 GB | 645.7 GB | 464.3 GB | **5.14 GB fit** | 3.93 GB fit |
| 8 GB | 645.7 GB | **7.25 GB fit** | 5.14 GB fit | 3.93 GB fit |
| ≥11 GB | 10.09 GB fit | | | |

1. **Cuantizar es de primer orden, política de segundo.** Cuantizar a int4 (S3) convierte un budget inviable (2-3GB) en casi-fit y un budget de 4GB en fit perfecto. Ninguna política online logra eso: su mejor régimen feasible en spill denso es LRU (0% hits).
2. **La política model-aware solo paga donde hay sparsity aprovechable en el espacio de bloques** (MoE: valle feasible real, G2-Exp1/A4) — no es un sustituto de la cuantización en un denso.
3. **El orden óptimo para el dueño del teléfono:** primero cuantizar hasta que `WS_cuantizado ≤ budget` (regla: budget ≥ 2.2GB pesos + KV + activaciones), y solo si el modelo cuantizado *aun* no cabe (o la pérdida de calidad de int4 prohibida, G2b), invertir en política — que entonces operaría sobre el WS reducido.
4. Corolario cuantificado: con 4GB de RAM, bf16 es inutilizable (645GB transferidos), int4-todo es perfecto (3.93GB 1×). La cuantización no es "una optimización más": es la diferencia entre funcional e infuncional.

## Caveats metodológicos

- **Granularidad:** el sim es a granularidad de bloque. Cuantizar el PLE a int4 aquí **no** modela la esparcidad de filas (esa la captura el OS a 4KB por sí solo, ~11MB gratis en G1) — modela el caso "política que transfiere bloques ya cuantizados". Comparación honesta: contra S0 a igual budget.
- **KV agregado (convención Fase 0.7):** el sim cobra KV por toque del bloque agregado `L{i}.kv` (~67MB/layer/step en esta traza, 2013MB de WS, 1920 toques → la mayor parte de los 645GB de S0). El KV **no es escalable** por cuantización de pesos (queda bf16) — por eso el umbral S3 es 3.93GB y no 2.21GB. En un engine real el KV se pagina por páginas y crece con el contexto; aquí es una aproximación agregada conservadora. Los umbrales "weights-fit vs sim-WS-fit" difieren exactamente en estos ~2GB de KV: S3 weights-fit = 2.21GB, sim-WS-fit = 3.93GB.
- **Calidad no medida (G2b):** int4 real cambia la salida del modelo; esta simulación mide solo bytes. La pregunta "¿int4 calidad-aceptable?" es empírica y pendiente.
- **No re-corre modelo:** los streams son los de bf16; el reescalado solo cambia tamaños, no secuencias de acceso (válido porque la cuantización por-familia no cambia el routing ni la arquitectura).

## Verificación

- 18/18 tests simulate verdes (incl. 3 nuevos de `scale_blocks`: identidad, por-componente, weights+override).
- Bug encontrado en este análisis: `cli.py` uppercasing de factores vs `scale_blocks` buscando `weights` en minúscula → S3 corría como identidad. Fix: normalización a MAYÚSCULAS dentro de `scale_blocks` + test.
- Reproducibilidad: misma traza (53c82d2b), mismos budgets, CSVs en `/tmp/g2-*.csv`.
