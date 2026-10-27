# La tesis intelectual del Observer: la inferencia LLM como problema de working set

Fecha: 2026-10-26 · Síntesis de Fases 1–2 (ADR-0069, `fase1-engine-real.md`, `fase2-evict-pura.md`)
· Estado: marco interpretativo — lo marcado **[establecido]** tiene medición directa en
hardware; lo marcado **[hipótesis]** es predicción falsable pendiente.

## 1. La descomposición estructural: núcleo estable vs. cola rotatoria

La inferencia autoregresiva de un transformer toca pesos de dos maneras
computacionalmente distintas, y Fase 1 las separó con datos reales:

1. **Núcleo estable y pequeño** (~1–2GB): attention, router, normas, la fila del
   embed del token actual, lm_head. Se toca *cada* token. **[establecido]** El
   LRU del OS lo encuentra en ~8 tokens y deja de fallar por completo
   (perfil por token: 0MB desde t9 en adelante). Sobre esta parte ninguna
   política de usuariospace puede mejorar al kernel: el baseline ya es óptimo.
2. **Cola rotatoria grande** (80–90% de los bytes): en denso, las capas FFN en
   secuencia; en MoE, los expertos que elige el router. Toda la pregunta
   interesante de gestión de memoria vive aquí, y se reduce a una sola
   variable: **¿cuánta localidad tiene el patrón de acceso de la cola?**

## 2. La localidad del routing como variable fundamental

La inferencia de un LLM es un **algoritmo de streaming sobre una matriz de
pesos de solo-lectura**, donde el patrón de acceso no es arbitrario: en MoE lo
decide la propia red vía el router. "¿Cuántos pesos distintos toca una corrida
de N tokens?" es entonces una **propiedad medible de la distribución de
routing** — un hecho empírico sobre cómo el modelo usa su propia memoria.

Medición directa (Fase 2, `policy.jsonl`):

- **[establecido]** A 64 tokens, OLMoE-1B-7B toca ~7253MB únicos de 14570MB
  a-priori — ~50% de los expertos aparecen. La cuenta combinatoria lo explica:
  8 sorteos top-k × 16 capas × 64 tokens = 8192 draws sobre 1024 expertos →
  cobertura esperada ≈ total. El routing es **plano en horizontes cortos**.
- **[establecido]** El working set tocado W(t) crece con los tokens (saturación
  de `resident_bytes` a 7253MB @64t en todos los budgets ≥ 8GB).
- **[hipótesis]** La forma de W(t) (¿sublineal/log por localidad emergente del
  router, o lineal hasta saturar los 1024 expertos?) determina matemáticamente
  cuándo existe spill y con qué intensidad. Es medible con las trazas del
  engine a {128, 256, 512} tokens.

## 3. La frontera de utilidad: W(t) > RAM

La consecuencia directa de (2) es una frontera, no una preferencia:

> **Toda ventaja de una política de residencia model-aware está acotada por
> max(0, W(t) − RAM) sobre la cola, no por la inteligencia de la política.**

- Donde W(t) ≤ RAM, no hay spill: el LRU del OS mantiene todo lo tocado y
  cualquier política es un no-op. **[establecido]** — Fase 2: a b≥8 y 64
  tokens, hlwss-evict emitió cero madvise y fue bit-a-bit os-lru.
- Donde W(t) > RAM, el orden de desalojo de la cola importa — y ahí el kernel
  sin conocimiento del modelo compite contra una política que sí lo tiene.
  **[establecido]** — Fase 2 b=6: evict −7% vs LRU (único punto medido con
  spill real, n=1, caché tibia).
- **[hipótesis]** Si W(t) crece lo suficiente con los tokens, el valle que el
  simulador predijo en [8,11]GB aparece en corridas largas, desplazado en el
  eje de tokens. Test: OLMoE b=8 × tokens {64,128,256,512} × {lru, evict}.

## 4. La jerarquía unificada de trade-offs

Cuantización, política de residencia y paging del OS son **el mismo trade-off
—bytes transferidos vs. calidad/latencia— en niveles distintos de la
jerarquía de memoria**. Fase 1/2 rankeó los levers con medición en hardware:

| Lever | Orden | Evidencia |
|---|---|---|
| Cuantización (comprimir la cola) | **Primero** — 2× lineal en bytes, independiente de localidad | gemma3n/Qwen1.5: FIT cuantizado es el único régimen sin thrash (G2, ADR-0068) |
| Política de residencia (reordenar la cola) | **Segundo, condicionado** — existe solo si W(t) > RAM; baseline del OS fuerte | Fase 1: LRU real converge a 0-fault en ~8 tokens; Fase 2: evict ≈ LRU salvo b=6 (−7%) |
| Prefetch agresivo (traer especulativamente) | **Negativo** — convierte orden de evicción en transferencia obligatoria | Fase 1: pin 2× peor en 3 arquitecturas; Fase 2: quitar WILLNEED recupera el 100% |

Formulación en una frase:

> *La inferencia de un LLM es un streamer con un núcleo caliente minúsculo y
> una cola de pesos cuya localidad es una propiedad medible del routing; una
> política de memoria model-aware solo puede ganar sobre la cola y solo cuando
> la cola excede la RAM.*

## 5. Por qué perdimos 2× antes de entender esto

El marco explica retroactivamente los tres resultados de Fase 1:

1. Pin+WILLNEED atacó el **núcleo con prefetch** (tormenta en prefill,
   bloques especulativos nunca re-tocados) en vez de la cola con evicción →
   transferir ~1.1× el modelo entero sin importar el budget.
2. El simulador cobraba el **WS a-priori** (14.57GB) en vez de W(t) tocado →
   inventó spill donde no había (valle fantasma en [8,11]GB a 64 tokens).
3. El LRU del simulador era FIFO sin readahead ni caché tibia → pintó el
   baseline 2.5× peor de lo que el kernel real es → fabricó una victoria de
   política que era artefacto.

## 6. Conexión con teoría de sistemas

El marco no es nuevo en su esqueleto — es el **working set model de Denning
(1968)** aplicado a pesos neuronales: thrash aparece cuando W(t) supera la
memoria asignada, y la política óptima de reemplazo (OPT, Belady) requiere
conocimiento del futuro. Lo que el proyecto aporta es la especialización:

- En LLMs, el "futuro del patrón de acceso" está parcialmente disponible en
  dos fuentes: el **router** (el próximo experto top-1 se conoce un paso
  antes de usarse — oracle parcial real, no asintótico) y la **estructura**
  (núcleo vs. cola es estática por arquitectura).
- En MoE la localidad del working set no es una propiedad del programa sino
  **una propiedad aprendida del modelo** — medible por capa, cambiable por
  fine-tuning. Eso abre una puerta que el OS clásico no tiene: *modificar la
  localidad del patrón de acceso, no solo reaccionar a ella* (p. ej. routers
  entrenados con regularización de localidad, clustering de expertos por
  archivo).

## 7. Predicciones falsables del marco

1. **[RESUELTA 2026-10-26 — refutada, ver `fase3-horizonte-largo.md`]** OLMoE
   b=8 × tokens {64,128,256,512}: W(t) satura en 7253MB antes de t=64 (cero
   bloques nuevos entre t=64 y t=512 — localidad fuerte del router en
   greedy, no plana), evict ≡ lru en todo horizonte. El valle no existe a
   b=8 en ningún horizonte; la frontera de utilidad queda en budget <
   ~7.25GB para este modelo/prompt/modo.
2. La curva W(t) medida debe ser monótona no-decreciente, con derivada que
   depende de la entropía del routing por capa (capas de baja entropía →
   W(t) se satura temprano).
3. Si (1) da win creciente, el techo de cualquier política online está entre
   el −7% medido (evicción pura) y el −34.9% del oracle del simulador
   (prefetch perfecto) → el siguiente lever es el oracle parcial del router
   (prefetch top-1 acotado).
4. Si (1) da empate persistente, el ranking de levers del §4 es definitivo
   para 16GB/M2 y el proyecto pivota a cuantización + embed desagregado
   (0.7GB de embed por 1 página usada por token es el desperdicio
   estructural más grande restante).
