# Fase 2 — Política de evicción pura (hlwss-evict): hallazgos en hardware

Fecha: 2026-10-25 · Continúa ADR-0069 + `ai-notes/findings/fase1-engine-real.md` · Branch `ex-observer-llm`

## 1. Qué se probó

Fase 1 dejó una pregunta abierta (puerta §7.1 del doc de Fase 1): ¿cuánto de la
penalización 2× de `hlwss-pin` era del **prefetch WILLNEED** y cuánto del pinning
mismo? Fase 2 implementa `hlwss-evict`: misma contabilidad de frecuencia running,
mismo pin set, misma evicción DONTNEED de fríos — pero **cero WILLNEED**
(`Policy.prefetch = name == "hlwss-pin"`, engine.py:262).

24 corridas nuevas, intercaladas en pares {os-lru, hlwss-evict} bajo las mismas
condiciones de máquina: OLMoE 6/8/9/10/11/12GB × 2 (12), Qwen3-0.6B 0.7GB × n=5 × 2
(10), gemma-3n-E2B 6GB × 2 (2). Total acumulado del engine: 54 reportes
(`traces-engine/*/report.json`).

## 2. Resultado central

**hlwss-evict recupera exactamente la penalización de hlwss-pin y colapsa sobre
os-lru, con una ventaja pequeña (−7%) solo donde el cap de evicción realmente ata.**

| OLMoE b=6 (cap ata) | GB transferidos | advises |
|---|---|---|
| os-lru (n=2) | 8.05 / 8.17 | 0 |
| **hlwss-evict** | **7.48** | 1219 DONTNEED, 0 WILLNEED |
| hlwss-pin (Fase 1) | 15.11 | 1219 DONTNEED + 27136 WILLNEED |

Evict = pin ÷ 2.02, evict = lru × 0.92. La conclusión de Fase 1 se confirma con
precisión quirúrgica: **el 100% de la penalización 2× era el prefetch WILLNEED**
(tormenta de prefill + prefetch de bloques que el routing flat nunca re-toca).
La componente "política" (freq + pin + DONTNEED) es esencialmente gratuita:
donde no ayuda, no estorba.

## 3. Descubrimiento estructural: el WS real tocado es 7253MB, no 14.57GB

Las trazas `policy.jsonl` muestran que `resident_bytes` (acumulado único de bloques
tocados) **satura en ~7253MB en TODOS los runs OLMoE con budget ≥ 8GB**, y en
5989466112 B (5.58GB, cap 6GB) en b=6:

- A 64 tokens con este prompt, el working set **únicamente tocado** es ~7.1GB —
  la mitad del WS a-priori del registry (14.57GB) que cobran el sim y el flag
  `fit` del engine.
- Consecuencia: el cap de evicción `resident_bytes > budget` **nunca ata a
  b≥8** → hlwss-evict emitió 0 madvise en b=8–12 y es *bit a bit idéntico* a
  os-lru. Las diferencias de GB en ese rango (±5%) son el piso de ruido
  pareado (E3/E8), medido ahora con dos políticas equivalentes como control
  perfecto: lru reps @8 = 7.77/7.82, @12 = 7.15/7.84.
- El valle del sim en [8,11]GB (win −15.7% @8GB predicho) **no existe en
  hardware a 64 tokens** porque no hay spill real: el WS tocado ya cabe. El
  único punto del valle con spill verdadero en este setup es b<7.1GB, y ahí
  la ganancia real medida es −7% (b=6), no −15.7%.

Esta es la segunda divergencia estructural sim-vs-hardware (la primera fue el
LRU pesimista del sim): **el sim cobra WS a-priori; el hardware solo paga lo
tocado**. A más tokens el WS tocado crece (routing flat → los 1024 expertos
eventualmente), así que el spill puede aparecer en corridas largas — no medido
aquí (ver §6).

## 4. Cross-checks

**gemma3n-E2B b=6** (denso, thrash real): lru 288.5/288.7, evict 286.2
(−0.8%, dentro del ruido; 2816 evicciones), pin 419.3 (Fase 1, 1.46× peor).
En denso la evicción dirigida tampoco rescata: el stream es secuencial, no hay
hot set que proteger, y pin+WILLNEED seguía siendo el único perdedor.

**Qwen3-0.6B 0.7GB** (régimen caché-tibio): sin señal usable — las reps dan
0–1.2GB con CV enorme y `budget_eff` negativo en algunas reps (el cálculo de
presión oscila con la memoria de otros procesos; inofensivo aquí). Confirmación
adicional de E3/E8: solo rankings pareados con spill real son interpretables.

## 5. Mecanismo del win −7% en b=6

Con budget 6GB < WS tocado 7.1GB, os-lru deja que el propio reclaim decida qué
expertos sobreviven; hlwss-evict fuerza DONTNEED sobre los ~370 expertos más
fríos (freq running) apenas el resident supera el budget, liberando caché para
el hot set estructural (~14% activación). 1219 evicciones concentradas en los
primeros tokens; steady state idéntico de residencia bajo ambas políticas
(confirma E2 de Fase 1: la diferencia está en *cómo se llega*, no en el estado
final). El tamaño del efecto (−7%) es coherente con "el LRU ya es casi óptimo;
solo se corrige su fricción inicial de warmup".

## 6. Caveats y qué NO se revisó

- b=6 evict es n=1: el −7% excede el spread de las reps lru (1.5%) pero la
  replicación en caché fría (con `purge`) sigue pendiente — E8 sigue operando.
- Tokens >64: el WS tocado crece con routing flat; el punto donde el WS tocado
  supera cada budget (y el cap de evicción vuelve a atar) **no se midió** y es
  la pregunta obvia siguiente (p. ej. OLMoE a 256–512 tokens en b=8–10).
- Qwen3 queda descartado como sujeto de política: demasiado pequeño, caché tibio.
- El cap usa el budget nominal, no `_PIN_FRAC × budget` (engine.py:309): a b=6
  el pin set (~0.9×6GB en expertos) deja solo ~0.6GB para el resto del resident.
  Funciona, pero el margen es fino; si el pin creciera (más tokens), el cap
  podría volverse inalcanzable y la política degenaría a no-op protegido.

## 7. Puertas que esto cierra y abre

1. **Cerrada: prefetch agresivo online**. WILLNEED por touch está muerto como
   estrategia (2× penalización, tres arquitecturas). Cualquier prefetch futuro
   debe ser acotado y bajo demanda real (puerta §7.2 de Fase 1: solo top-1 del
   router, solo si no residente).
2. **Abierta: spill real en corridas largas**. El valle del sim puede existir
   en hardware, solo a más tokens. Test directo: OLMoE b=8, tokens ∈ {64, 128,
   256, 512}, evict vs lru. Si evict−lru crece con tokens, la política sí tiene
   frontera de utilidad; si no, el LRU del OS es el techo y el proyecto pivota
   a cuantización (G2b) y embed desagregado (§7.5 Fase 1).
3. **Abierta: FIT online real**. El engine ya reporta `fit` con WS a-priori
   (conservador, E4 Fase 1); la saturación de resident en 7253MB a 64 tokens
   sugiere un FIT tocado-dinámico medible en vivo — la timeline de residencia
   puede darlo.
4. **Lección de método**: dos políticas equivalentes (evict ≡ lru a b≥8) dan
   el control de ruido ideal: ±5% pareado. Cualquier futura comparación debe
   exceder este piso para declarar efecto.
