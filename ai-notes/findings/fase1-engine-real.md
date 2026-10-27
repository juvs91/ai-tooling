# Fase 1 — Engine real de residencia: hallazgos sim-vs-hardware

Fecha: 2026-10-25 · ADR-0069 · Branch `ex-observer-llm` · Engine: `vendor/hlwss/hlwss/engine.py`

## 1. Qué se midió (método)

Inferencia real en CPU sobre pesos file-backed (`np.memmap` PROT_READ MAP_PRIVATE,
vistas zero-copy por tensor), budget de RAM impuesto por buffer de presión
(`torch.empty(fill_=1)` vivo toda la corrida), y bytes transferidos medidos como
`Δru_majflt × 16384` (pageins del proceso, por token y total). Residencia real por
bloque/familia vía `mincore` por-vnode (el page cache es por archivo, no por mmap).
Políticas: `os-lru` (no-op, el OS decide) y `hlwss-pin` (pin online por frecuencia
running + `madvise(WILLNEED)` al tocar + `madvise(DONTNEED)` en evicción de fríos).

28 corridas de experimento + 2 de calibración: OLMoE-1B-7B 6–12GB × 2 políticas (12),
gemma-3n-E2B 6/8GB × 2 (4), Qwen3-0.6B 0.7GB × 2 políticas × n=5 (10),
Qwen1.5-MoE-A2.7B 12GB × 2 × 32 tokens (2, estiramiento).
Prompt p0 greedy de `prompts/validation.json`, 64 tokens (32 estiramiento).
Slack calibrado: 0.5GB (peak RSS sin presión 3.18GB − pesos residentes 2.87GB = 0.31
+ margen). Trazas: `vendor/hlwss/traces-engine/<run_id>/{report.json,residency.jsonl,policy.jsonl}`.

## 2. Tabla cruz sim-vs-hardware (entrega central)

OLMoE-1B-7B, 64 tokens, GB transferidos. Sim = replay traza 9951188c (p0, ADR-0065/0066);
hw = pageins reales ×16KB.

| budget GB | sim lru | sim hlwss | sim hlwss-adaptive | **hw os-lru** | **hw hlwss-pin** |
|---|---|---|---|---|---|
| 6  | 33.8 | 22.0 (inf) | 29.8 | 8.77 | 16.22 |
| 8  | 22.2 | 18.7 (−15.7%) | 18.7 | 8.34 | 16.41 |
| 9  | 19.2 | 16.2 | 16.2 | 8.10 | 17.29 |
| 10 | 15.3 | 14.1 | 14.1 | 9.07 | 16.34 |
| 11 | 13.3 | 12.9 | 12.9 | 7.84 | 16.32 |
| 12 | 11.9 | 12.6 | 11.9 (fit) | 7.68 | 16.64 |

**Qué se reproduce y qué no:**

1. **La forma del valle se reproduce**: en ambos mundos los bytes transferidos bajan
   monótonamente con el budget (bajo LRU: 33.8→11.9 sim, 8.8→7.7 hw). La dirección
   de la predicción estructural es correcta.
2. **El ranking de políticas se INVIERTE**: el sim decía "hlwss-pin gana −15.7% @8GB";
   el hardware dice "pin pierde ~2×" (16.3–17.3GB vs 7.7–9.1GB en todo el rango).
3. **El LRU real es ~2.5× mejor que el LRU del sim** (8.3 vs 22.2GB @8GB). El LRU del
   sim es FIFO a granularidad de bloque sin readahead que cobra cada touch frío; el OS
   real tiene readahead, página caliente y evicción por uso real.
4. Consecuencia: **la mayor parte del "win" de política del sim era artefacto de su
   propio LRU pesimista**, no una ganancia real del pinning.

## 3. Mecanismo (por qué pin pierde en hardware)

Perfil por token a 8GB (pageins por fase):

| política | prefill | decode t1–8 | decode t9–32 | decode t33–63 | EMBED residente (fin) |
|---|---|---|---|---|---|
| os-lru | 7.4GB | 945MB | **0MB** | **0MB** | 0.01% (~1 página: la fila caliente) |
| hlwss-pin | **14.9GB** | 1185MB | 9MB | 268MB | **100%** (0.7GB fijos) |

- **Tormenta WILLNEED en prefill**: el path MoE fused ejecuta topk × prompt_len
  expertos por capa en prefill (272 filas/capa a prompt 34); la política advise
  WILLNEED sobre cada uno → el OS prefetchea cientos de bloques de expertos en
  paralelo a la computación. Prefill de pin = 2× el de LRU.
- **Prefetch de bloques nunca re-tocados**: con routing flat (Exp1 P4: top-8 cubren
  ~51%), la frecuencia running a 64 tokens termina tocando casi todos los 1024
  expertos; el pin set rota y cada experto recién pinneado se pagina especulativamente.
  Total pin ≈ 16.3GB ≈ 1.1× WS total (14.57GB): **se transfirió esencialmente todo
  el pesado sin importar el budget**.
- **LRU converge a steady state sin faults**: tras ~8 tokens de warmup, el hot set
  (attn + ~14% expertos + lm_head) cabe en el budget efectivo y los faults por token
  son exactamente 0. El OS LRU ya *es* una política near-optimal para el hot set
  estructural (12.5% activación).
- **Desperdicio adicional**: pin mantiene EMBED 100% residente (0.7GB) donde LRU
  mantiene 1 página (la fila del token actual).

**Raíz del desacuerdo sim-vs-hardware**: el sim modela la política como *orden de
evicción* (pin protege del reclaim); `madvise(WILLNEED)` real es *prefetch activo*
con costo de transferencia propio que el sim no cobra. El sim también cobra el
LRU sin los mecanismos que hacen al LRU real bueno (readahead, caché tibio).

## 4. Verificaciones internas (E1–E4)

- **E1 (bytes ≥ único tocado)**: ✗ como cheque absoluto — 7.68GB hw @12GB < 11.86GB
  WS único tocado, porque las páginas del archivo ya estaban residentes entre corridas
  (caché tibio, ver §6). Se cumple solo en corridas frías.
- **E2 (residencia mincore ≥ pin declarado)**: ✓ — resident_frac ∈ [0,1] siempre;
  policy-resident ≤ budget (OLMoE b=6 pin: max 5.99GB ≤ 6GB; pin convergió a
  202 expertos cubriendo 0.9 de la frecuencia reciente). Detalle notable: la
  fracción EXPERT residente converge a lo mismo bajo ambas políticas (12.7% pin
  vs 12.5% LRU ≈ activación estructural) — la diferencia de costo está enteramente
  en *cuánto se transfirió* para llegar a ese steady state, no en el estado final.
- **E3 (reproducibilidad, Qwen3 n=5)**: ✗ en absolutos — os-lru CV=0.96
  (0.13–1.20GB entre reps idénticas), hlwss-pin CV=0.56. El ranking *dentro* de cada
  pareja sí es estable (pin > lru en 5/5 pares). Los GB absolutos de pageins no son
  reproducibles en una máquina tibia; el contraste pareado sí.
- **E4 (FIT no-op)**: ✓ por construcción — `fit=true` solo a 16GB (WS registry
  14.57GB > 12GB; el plan asumía WS único 11.86GB — corregido: el engine usa WS
  a priori completo, cerrando el caveat #1 de ADR-0066 con la señal conservadora).

## 5. gemma3n-E2B (cross-check denso)

gemma-3n-E2B-it, 64 tokens, GB transferidos. Sim = traza 0f1af5df (p0).

| budget GB | sim lru | sim pin (hlwss) | **hw os-lru** | **hw hlwss-pin** |
|---|---|---|---|---|
| 6 | 645.7 | 10.1 — **inf** | 309.8 | 450.2 |
| 8 | 645.7 | 10.1 — **inf** | 309.9 | 489.2 |

(sim pin = 10.1GB es lo que transferiría *si* cupiera: WS 10.09GB > budget →
**infeasible** en ambos budgets. Cada corrida hw duró ~630–690s ≈ 0.1 tok/s:
thrash denso real.)

Verdicts:

1. **El acantilado denso se reproduce**: hw LRU 310GB ≈ 0.48× sim 645.7GB
   (factor caché tibio, §6 E8), e **independiente del budget** (309.8 vs 309.9
   a 6 vs 8GB) — exactamente la firma del sim (645.7 en ambos).
2. **La política online no rescata al denso**: pin parcial (0.9×budget ≈ 5.4–7.2GB
   de un WS de 10.1GB) es *peor* que LRU (450–489 vs 310GB): los bloques pinneados
   ocupan espacio que el LRU adaptativo usaría para el stream realmente caliente,
   y WILLNEED añade la tormenta de prefetch (§3). Coincide con la señal del sim:
   pin > budget → infeasible.
3. Cierra el triángulo con G2 (ADR-0068): **en denso, cuantizar/fit es de primer
   orden y la política de residencia es de segundo orden a lo sumo** — el hardware
   lo confirma con thrash real.

## 5b. Estiramiento: Qwen1.5-MoE-A2.7B (32 tokens, 12GB)

Qwen/Qwen1.5-MoE-A2.7B, 32 tokens. Sim = traza 20e58cc0 (p0): régimen **FIT**
(WS único 9.3GB ≤ 12GB) → las 4 políticas no-op, 9.324GB predichos.

| | sim (FIT) | **hw os-lru** | **hw hlwss-pin** |
|---|---|---|---|
| GB transferidos | 9.3 | 152.5 | 325.8 |

1. **La inversión del ranking se reproduce en la 2ª arquitectura MoE**: pin
   2.1× peor que LRU (325.8 vs 152.5GB) — mismo mecanismo WILLNEED (§3).
2. **El régimen FIT del sim no es reproducible en hardware contendido**: el LRU
   real transfiere 16× la predicción FIT (152.5 vs 9.3GB). El sim FIT asume que
   el WS tocado queda residente para siempre; el OS real bajo presión global de
   swap (máquina con ~15GB de swap en uso durante el sweep) reclama páginas de
   archivo continuamente (weight_resident_end 3.44GB de ~27GB de archivo).
3. El engine declaró `fit=false` (WS a priori 27GB > 12GB) mientras el sim
   declaró FIT (WS tocado 9.3GB ≤ 12GB) — las dos señales de FIT son
   conservadora-vs-optimista; en una máquina sin presión ajena el LRU habría
   convergido al FIT del sim, en ésta no.

## 6. Caveats

- **E5 (swap vs file)**: `ru_majflt` cuenta swapins del buffer de presión y del KV
  anónimo además de pageins de archivo; ambas políticas comparten el sesgo, el
  contraste pareado lo cancela.
- **E6 (granularidad)**: 16KB de página vs bloques lógicos; un bloque pequeño caliente
  obliga a mantener página completa.
- **E7 (KV anónimo)**: fuera del mecanismo de pesos por diseño (ADR-0069).
- **E8 (caché tibio, NUEVO)**: el page cache por-vnode sobrevive entre corridas;
  los GB absolutos de pageins son Δ respecto al calor acumulado del sweep, no el
  costo de una corrida fría. No se pudo medir frío (sin `purge`/sudo). Mitigación
  usada: ranking pareado + perfiles por token.

## 7. Puertas abiertas (decisiones que esto habilita)

1. **La política correcta en hardware no es pin+WILLNEED**: el dato sugiere probar
   "LRU + DONTNEED dirigido" (evicción explícita de lo que la política sabe frío,
   SIN prefetch) — el sim puede modelarlo barato (política de evicción pura).
2. **WILLNEED solo bajo demanda real**: prefetch solo del experto que el router
   acaba de elegir (top-1, no top-k) y solo si no residente — convierte el prefetch
   en oracle parcial acotado (el sim tiene el oracle: −34.9% @8GB es el techo).
3. **FIT online desde la timeline de residencia**: la timeline mincore por token
   (entregable de exploración) muestra la convergencia a steady state en ~8 tokens;
   la señal FIT a priori del registry ya es conservadora y funcional (E4).
4. **El LRU del sim necesita readahead/caché tibio para ser comparable** — sin eso,
   cualquier política del sim "gana" contra un LRU artificialmente malo.
5. **EMBED como caso de estudio de sobre-residencia**: 0.7GB por una tabla de la que
   se usa 1 página por token — política de desagregación embed por filas (ADR futuro).

## 8. Qué NO se revisó

- Alineación por bloque fría (fault attribution ordenada por capa L0-first domina
  en caché tibia; la comparación justa requiere corridas frías, ver §6 E8).
- Tokens >64 (la convergencia a 0-faults sugiere estabilidad, no verificado).
- Cuantización real de pesos (G2b queda como fase futura).
