# ADR-0066: Fase 1 preflight — política HLWSS adaptativa (régimen + shrink-to-fit)

- **Estado:** aceptado (staged 2026-10-13, branch `ex-observer-llm`)
- **Contexto:** ADR-0064 (cap R5), ADR-0065 (simulador Fase 0.7), ADR-0063 (int4)
- **Decisión:** añadir al simulador una cuarta política `hlwss-adaptive` con dos
  componentes mecánicos (no predictivos): detección de régimen FIT y pin
  shrink-to-fit. El engine real de Fase 1 (loader memmap) NO entra — este
  preflight es la justificación de su existencia.

## Contexto y justificación

El análisis del gap al oráculo (2026-10-08/13, documentado en
`ai-notes/findings/simulator-fase-07.md`) produjo **cinco NO-GOs con datos**
para el prefetch predictivo: recencia (window-recall 25% a K=128), 2Q (peor que
LRU), pin reducido (top-90% es óptimo), afinidad cross-token (Jaccard 0.679 <
0.710 azar) y afinidad cross-capa (recall@4 13.4% ≈ frecuencia 14.6% vs azar
10.2%). Conclusión estructural: el routing de expertos es muestreo de alta
entropía con señal únicamente de frecuencia (estática, ya explotada por el pin).

El valor marginal restante de Fase 1 es mecánico, no predictivo:

1. **Detección de régimen FIT.** En el sweep fino, hlwss pierde −6.1% a −8.6%
   en 8.75–9.5GB porque el pin+SLRU es overhead puro cuando el working set
   único (9.32GB) ya cabe en el budget: paga cold-load del pin y thrashing del
   cold segment. Recuperar eso vale hasta +8.6%.
2. **Pin shrink-to-fit.** El pin fijo (7.38GB) es infeasible bajo sí mismo:
   a 6–7.25GB la política estática no corre (filas INFEASIBLE del sweep). Un
   pin cuya cobertura se recorta hasta caber sí corre, y en esa zona LRU es
   muy malo (14.8GB transferidos a 7.25GB).

Si el replay con `hlwss-adaptive` materializa ambos wins, el engine de Fase 1
tiene justificación medida; si no, queda descartado con el mismo rigor con el
que cayó el prefetch.

## Decisión técnica

- `hlwss-adaptive` = política unificada de Fase 1:
  - **FIT** (working set único tocado ≤ budget): degradar a LRU puro,
    `pinned=∅`, sin cold-load. Métrica `regime: "fit"`.
  - **SPILL**: pin con cobertura target 0.9 **acotado por presupuesto**
    (`pin_cap = 0.9 × budget`); la cobertura efectiva resultante puede ser
    menor (shrink-to-fit). Métricas `regime: "spill"`, `coverage` efectiva.
  - Contabilidad honesta inalterada: el pin paga su carga en frío; primer
    toque pinneado = miss; bloques fantasma excluidos (pin ∩ tocados).
- `hlwss` estático se conserva como ablation: cuantifica qué compra la
  adaptatividad (diferencia adaptativa − estática por budget).
- `_PIN_BUDGET_FRAC = 0.9` queda fijo en código; su sensibilidad es análisis
  posterior (sweep de β), no parte de esta decisión.

## Caveats explícitos

1. **La señal FIT es hindsight en el simulador** (working set único *tocado*
   de la traza). En el engine real la señal equivalente es registry total +
   modelo de KV growth — conocida a priori, no mirada al futuro. El sim
   acota el win por arriba en régimen FIT (detecta antes de lo que el engine
   podría). Documentado para no inflar el resultado.
2. **Base no-expert > budget** sigue siendo infeasible (marcado, no crash):
   si los pesos no-expert no caben, el modelo no corre en ese budget con ese
   formato — el lever correcto ahí es formato (int4, ADR-0063), no política.
3. β=0.9 y `_HOT_FRAC`=0.8 sin sweep en esta fase: si el preflight valida,
   el sweep de umbrales es la siguiente pregunta, con la misma metodología.

## Consecuencias

- `POLICIES` pasa a 4 (lru / hlwss / hlwss-adaptive / oracle); el CLI acepta
  la nueva política; tests nuevos cubren FIT, shrink-to-fit, infeasible-base,
  cold-load contable y paridad con la estática en régimen spill.
- LOC: +~55 en simulate.py, +~3 cli.py, +~70 tests → ~2,220/2,500 del cap R5.
- Si el sweep no materializa (a) ni (b), Fase 1 engine queda sin
  justificación y se documenta como negativo honesto (mismo estándar que la
  dieta LOC y el early-exit E1-A).
