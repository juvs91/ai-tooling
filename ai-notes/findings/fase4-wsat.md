# Fase 4 — W_sat como propiedad del modelo: caracterización y cierre del arco

Fecha: 2026-10-26 · ADR-0070 (flag `--temperature`) · Continúa tesis §3/§7
· 7 corridas nuevas (sampling 512t, greedy-p1 512t, Qwen1.5-MoE 128t,
  replicación b=6 en caché fría ×2, smokes) → total acumulado 69 reportes.

## 1. W_sat por condición (resident_bytes final, engine hlwss-evict)

| condición | W (MB) | vs base | señal |
|---|---|---|---|
| OLMoE greedy p0, 512t | 7253 | — | satura ≤t64 (Fase 3); 0 evictions @b=8 |
| OLMoE greedy p1 (prompt diverso), 512t | 6797 | −6.3% | W depende del prompt (prefill fija la cobertura) |
| OLMoE **sampling T=1**, 512t | **>7629 (cap)** | W cruza budget | **9177 evictions, 382.7GB transferidos** |
| Qwen1.5-MoE greedy p0, 128t | 5981 | — | 0 evictions @b=12; W ≪ archivo 27GB |

1. **W_sat es real, medible y estable**: OLMoE greedy p0 da 7253MB en 10+
   corridas independientes (budgets 8–12GB, horizontes 64–512t). Es una
   propiedad del modelo+prompt+mODO, no de la corrida.
2. **El prompt importa poco en greedy** (−6% entre p0/p1): la cobertura la
   fija sobre todo el prefill (34 posiciones × top-8 × 16 capas ≈ 4352
   draws ya ejercitan la mayoría de expertos reutilizables); el decode greedy
   añade casi nada.
3. **El modo importa muchísimo**: sampling T=1 empuja W por encima del
   budget 8GB. La localidad del greedy (bucles de ruta por capa) era la que
   mantenía W bajo; al muestrear, el decode deja de repetir y el WS crece.
4. **Transición de fase abrupta y thesis-consistente**: greedy W=7253MB
   (bajo el cap 7629MB) → 8GB transferidos, vida normal. Sampling W>cap →
   9177 evicciones de churn (≈18/token), 382.7GB transferidos (**48×**).
   Un cruce de ~5% sobre el presupuesto convierte una corrida sana en
   thrash. El costo de memoria es discontinuo en W=RAM — exactamente lo que
   el working-set model de Denning predice y lo que la tesis §3 formuló.

## 2. Replicación en caché fría del contraste b=6 (Fase 2)

Flush de caché por presión (12GB anon dirty, sin sudo/purge) antes del par:

| | GB transferidos |
|---|---|
| os-lru | 8.49 |
| hlwss-evict | 8.12 (**−4.4%**) |

Dirección confirmada en frío (−4.4% vs −7% tibio): la ventaja de la política
vive en el componente de refaults (contención), no en los misses de
compulsión — en frío ambos políticas pagan los mismos pageins obligatorios y
el margen se reduce. El −7% tibio de Fase 2 era mezcla de efecto real +
caricia de caché; el efecto real parece ~4–7%.

## 3. Caveats

- **Fase-1 vs Fase-2+**: la única traza t=8 con 7529MB (>7253MB) es de Fase
  1, anterior a los fixes de conteo drain/bind (Δ=276MB, 3.8%); las
  mediciones W de Fase 2–4 son internamente consistentes. No mezclar fases.
- Qwen1.5-MoE: W=5981MB medido con presión global de swap de la máquina
  (~15GB en uso, caveat E5/E8 de Fase 1); el thrash de 565GB ahí no es
  atribuible al budget del engine sino a la presión ajena — la medida W
  (bookkeeping de tocados) sigue siendo válida.
- Sampling: n=1, T=1.0, seed fijo 42. La magnitud exacta del cruce W>RAM
  varía con T y prompt; la dirección (muestreo ⇒ W crece ⇒ thrash) es la
  predicción estructural y se confirmó.

## 4. La puerta cerrada: política vs LRU en el régimen de thrash (2026-10-26)

Pareado b=8 × sampling T=1.0 × 128t (W>RAM, churn real):

| política | GB | evicciones |
|---|---|---|
| os-lru | 80.0 | 0 |
| hlwss-evict | 78.9 | 1959 |

**Empate** (−1.3%, dentro del piso de ruido ±5%): ambas churnean a
~0.62GB/token. Extendido al dato de la corrida 512t (382.7GB ≈ 0.75GB/token,
misma tasa), el kernel reclama bajo presión tan bien como la mejor política
online implementable con madvise.

Patrón completo de los 3 pares con spill real: evict gana siempre pero
poco — b=6 tibio −7%, b=6 frío −4.4%, b=8 sampling −1.3%. Efecto débil
consistente (3/3), nunca de dos dígitos, que se diluye a medida que el churn
domina (en thrash puro el orden de desalojo deja de importar: todo se
toca poco y se rechaza todo).

**Veredicto final**: la política de residencia model-aware NO le gana al
LRU del OS en ningún régimen medible. La tesis §4 queda como la conclusión
del proyecto: cuantizar (primer orden) > política de residencia (segundo
orden, ganancia ≤7% condicionada a spill) > prefetch (negativo).

## 5. Estado del arco científico

- Tesis §3 (frontera W>RAM): **confirmada** con transición de fase medida.
- Tesis §4 (jerarquía de levers): intacta — cuantización > residencia >
  prefetch.
- Tesis §7 predicción 1 (valle en tokens): refutada en greedy (Fase 3),
  **explicada** por localidad de greedy; el valle sí existe en el espacio
  (modo × budget), no en el eje tokens.
- Entregable independiente: W_sat medible por modelo/prompt/modo con el
  engine (protocolo: 1 corrida hlwss-evict con budget > W esperado, leer
  `resident_bytes` final de policy.jsonl).
