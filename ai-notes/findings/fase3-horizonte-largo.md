# Fase 3 — Horizonte largo: ¿aparece el spill con más tokens?

Fecha: 2026-10-26 · Continúa `tesis-intelectual-working-set.md` §7 predicción 1
· 8 corridas: OLMoE-1B-7B, budget 8GB, tokens {64,128,256,512} × {os-lru,
  hlwss-evict}, pares intercalados (`traces-engine/*/report.json`, total
acumulado 62 reportes).

## 1. Resultado

| tokens | lru GB | evict GB | evicciones (evict) | resident final (evict) |
|---|---|---|---|---|
| 64  | 8.89 | 8.44 | 0 | 7253MB |
| 128 | 8.37 | 8.16 | 0 | 7253MB |
| 256 | 7.63 | 7.87 | 0 | 7253MB |
| 512 | 8.25 | 8.89 | 0 | 7253MB |

1. **Cero evicciones en las 8 corridas.** El cap `resident_bytes > budget`
   nunca ató: W(512) < 7GB efectivo. El spill que el simulador predijo para
   [8,11]GB **no aparece en ningún horizonte**.
2. **El working set satura: `resident_bytes` = exactamente 7253MB en
   t=64, 128, 256 y 512.** Entre el token 64 y el 512 no se toca un solo
   bloque nuevo. El routing en greedy decode reutiliza intensamente el mismo
   subconjunto de expertos — la localidad es **fuerte**, no plana. La cuenta
   combinatoria de "cobertura total" (Fase 2 §3) asumía sorteos uniformes;
   el router concentra: ~660/1024 expertos nunca aparecen.
3. **Los pageins no crecen con los tokens** (7.6–8.9GB en todo el rango;
   pares dentro del piso de ruido ±5% medido en Fase 2). Tras el warmup, el
   decode añade ~0 faults: el steady state de 64 tokens se mantiene 448
   tokens más.
4. `weight_resident_end_gb` ≈ 2.4–2.7GB en todas las corridas: bajo presión
   el kernel retiene solo el hot set real (~1/3 del WS tocado).

## 2. Veredicto sobre la predicción falsable #1

**Refutada.** La hipótesis "el valle del sim existe desplazado a más tokens"
predecía que evict−lru crecería con tokens si W(t) superaba 8GB. W(t) no
supera 7.3GB nunca: satura antes de t=64 por localidad del router en greedy.

Consecuencia para la tesis (§3 de la tesis intelectual): la frontera de
utilidad W(t) > RAM, con este modelo/prompt/modo, queda en **budget < 7.25GB
sin importar el horizonte**. El único régimen medible con spill real en esta
máquina es b<7.25GB — y ahí (Fase 2, b=6) la ventaja de evict es −7%.

## 3. Caveats

- **Un prompt, greedy decode.** La localidad medida es la del texto generado
  (repetitivo bajo greedy + spill). Sampling con temperatura o prompts que
  recorran dominios diversos podrían expandir W(t). El engine lo mide igual
  (mismo protocolo, `--max-tokens` grande); pendiente si se quiere cerrar.
- La saturación exacta en 7253MB sugiere que el decode entra en bucle de
  expertos (misma ruta por capa token a token) — útil para el estudio de
  localidad pero es un régimen particular, no general.
- budget_eff osciló 6.86–8.41GB entre corridas (calor de máquina); el
  contraste pareado controla, como en Fases 1–2.

## 4. Qué sigue (decisión de proyecto)

Con horizonte cerrado, el ranking de levers de la tesis §4 queda como guía:

1. **Cuantización (G2b)** — el lever de primer orden, sin condiciones.
2. **Embed desagregado** — 0.7GB fijos de EMBED residentes (pin) por 1
   página usada por token (Fase 1 §3): el desperdicio estructural más grande
   identificado, atacable sin política de paging.
3. **Política de residencia** — reducida a su frontera real: budgets < W_sat
   (~7.25GB en este setup). Si el dispositivo objetivo tiene menos RAM que
   W_sat, el −7% @b=6 y el oracle parcial del router (prefetch top-1
   acotado, techo −34.9% del sim) siguen vivos; si no, es segundo orden
   puro.
4. **La curva W_sat como propiedad del modelo** — medir W_sat por
   modelo/prompt/modo (barato con el engine actual) sería el entregable que
   predice *a priori* si la política puede ganar en un dispositivo dado.
   Es, en sí mismo, un resultado publicable sobre localidad de routing MoE.
