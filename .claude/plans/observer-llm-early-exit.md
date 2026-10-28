# Plan E1 — Early-Exit Calibrado (probes post-hook + política de corte)

> **Estado:** EN REVISIÓN (versión 1)
> **Task ID:** hlwss-early-exit-e1-2026-10-02
> **Branch:** `ex-observer-llm` · **Código:** `vendor/hlwss/` · **ADR:** ADR-0062 (staging, se crea al implementar)
> **Motivación registrada en:** PRD v0.4 §4.1 (Jev — qué transfiere y qué no) y §4.2
> **Prior art:** CALM (Schuster et al., ACL 2021); "logit lens" (nostalgebraist 2020); DeeBERT (2020)
> **Depende de:** Fase 0 ✅ · **Se agenda DESPUÉS de Fase 0.5** (no compite con ella)

---

## 1. Pregunta de investigación

> En Qwen3-0.6B greedy, ¿la distribución de logits en la capa k predice el token final (capa 28), con qué confianza medible, y cuántos bytes/tok/s cuesta ganar por política de salida temprana?

**Por qué ahora es medible (y antes no):** Fase 0 entregó (a) loop de inferencia **manual** (no `generate()` — controlamos el forward capa a capa), (b) hooks por capa ya instrumentados, (c) contabilidad exacta de bytes por bloque (V1=0.0), (d) harness de calidad fijado (PRD §6). Los cuatro son los prerrequisitos; los tenemos.

**Recompensa conocida de antemano (dato Fase 0):** 1.2 GB leídos/token; capas 20–27 ≈ 170 MB/paso (attn+ffn). Decode memory-bound → cada capa saltada es ~6 MB/paso de ancho de banda devuelto. Saltar sistemáticamente en tokens "fáciles" es el mayor palanca de tok/s disponible **sin reentrenar ni cuantizar**.

## 2. Riesgo científico central (por qué esto es un experimento y no una feature)

**Acuerdo ≠ corrección.** Que `argmax(logits_cap_k) == argmax(logits_final)` no garantiza que el token sea bueno — podrían estar de acuerdo en el token equivocado, o el error temprano podría ser *auto-cumplido* (el estado residual contaminado arrastra a las capas siguientes). Por eso la secuencia es: **medir acuerdo (E1-A) → validar calidad (E1-B con harness PPL)**. Nunca se activa el corte por defecto.

## 3. Diseño — dos sub-fases

### E1-A — Probes observacionales (SOLO mirar, forward intacto)

**Mecánica:** modo `--probe-layers 4,8,12,16,20,24` en el adapter. Cada capa probada registra un post-hook adicional que:

1. Toma la salida del bloque `h_k` (estado residual, ya existe en el hook).
2. Aplica `lm_head` sobre el último token → logits intermedios.
3. Registra `(token_pos, layer, entropy, argmax_id, top1_prob, ==final)` en `trace.dists` (source="profiler").

Al final del run, compara contra el argmax real del token generado (known después del paso). Costo: 1 aplicación extra de lm_head (311MB de lectura) por capa probada por token. **Mitigación:** probe sobre 1-de-M tokens (`--probe-every M`, default 4) → overhead ≈ 311MB×6/4 ≈ 470MB/token ≈ +40% de tráfico; la corrida se marca `experiment=early-exit-probe` y **queda fuera de certificación V3** (es un experimento, no producción). Se mide el costo real con pareja on/off estilo V3.

**Entregables E1-A:**
- Curva de **acuerdo por capa**: % tokens donde argmax(h_k) == token emitido, por k ∈ {4..27}, con bandas por tipo de token (puntuación vs palabra, largo de palabra — el tokenizer nos lo da).
- Curva de **entropy vs acuerdo**: scatter + histograma; se ajusta umbral τ_k por capa (máximo acuerdo condicionado a cubrir ≥ X% de tokens).
- **Tabla τ\***: por capa, el τ de entropy que maximiza (bytes ahorrados × precisión). Train/test split por prompt (ajustar en prompts 0–2, validar en 3–4 — evita sobreajuste del umbral).

**LOC E1-A:** adapter probe mode ~70 (un método + flag + fila de dist), análisis `probe_report` ~50 (agregación + curvas en texto/markdown — sin plotting deps). **Subtotal ~120.**

### E1-B — Corte real (early-exit decode)

Solo si E1-A muestra acuerdo útil (criterio de entrada: existe τ con precisión ≥ 99% cubriendo ≥ 30% de tokens en test).

**Mecánica:** `run()` gana un modo `--early-exit τ_spec` donde el forward deja de ser `model(...)` monolítico:

```
h = embed(ids); position_ids = arange(...)
for i in 0..27:
    h = layer[i](h, position_ids, causal_mask, past_kv)   # loop manual por capa
    if i ∈ probes and entropy(lm_head(h_last)) < τ_i:      # probe post-hook en caliente
        break                                               # ← EL CORTE
logits = lm_head(final_norm(h)); nxt = argmax(...)
```

**Decisiones de diseño delicadas (cada una con mitigación):**

| Decisión | Riesgo | Mitigación |
|---|---|---|
| Loop manual de capas (en vez de `model.forward`) | Reproducir a mano la semántica de HF: position_ids, causal mask, RoPE, KV write | Qwen3Model.forward es un loop trivial de capas + norm final; verificar contra `model(...)` salvo argmax en modo "correr todo sin cortar" → V1/V4 deben seguir pasando con el loop manual (gate de entrada a E1-B) |
| Capa saltada = sin KV write | El token cortado no deja K,V → capas futuras lo ven menos | Es la semántica real del early-exit; el harness PPL lo juzga. Se documenta: cortes consecutivos acumulan deuda de KV |
| V1 con bytes dinámicos | El expected por paso ya no es constante | **V1-E generalización:** `expected_bytes(registry, cfg, d, ctx, exit_layer_t) = embed + lm_head + Σ_{i<exit} (attn+ffn+kv_i)`. El criterio ±10% aplica por paso con su exit_layer real (los eventos ya no mienten: las capas saltadas no emiten) |
| V4 bajo corte | ¿Determinismo con cortes? | Greedy + τ fijo + mismo input → mismas salidas de corte → misma tupla canónica. V4 sigue aplicando (y ahora valida también la política) |
| Tokens difíciles tras racha de fáciles | Degradación por contexto | Medir PPL por posición, no solo global |

**LOC E1-B:** loop manual + corte ~90, V1-E ~25, umbral/policy loader ~25. **Subtotal ~140.**

## 4. Calidad — primer uso del harness (PRD §6)

`wikitext2-ppl-slice-v1`: slice y stride fijos (se fijan en el ADR al implementar), PPL del baseline vs PPL bajo política τ\*. Métrica de reporte: **frontera ΔPPL vs bytes-ahorrados-por-token** (el observer la computa exacto: bloques no emitidos por paso). Políticas comparadas:

1. Baseline (sin corte)
2. Corte fijo en capa k* (la de mejor acuerdo E1-A)
3. Corte adaptativo por τ* (la propuesta seria)
4. Corte adaptativo + piso de "n cortes consecutivos máx" (anti-deuda-KV)

## 5. Verificación

1. **Gate de entrada E1-B:** loop manual sin cortes reproduce V1 ≤ 10⁻⁹ y V4 idéntico al forward monolítico → el loop manual es fiel.
2. E1-A: τ* validado en prompts holdout; reporte de curvas.
3. E1-B: V1-E ≤ 10% por paso; V4 reproducible; PPL reportada vs baseline; tok/s ratio (con y sin probe overhead).
4. `pytest` nuevo: `test_v1e.py` (expected dinámico con exit_layer), `test_probe_report.py` (acuerdo sintético), gate loop-manual-fiel.
5. `task-verify.sh` al cierre.

## 6. LOC y límites

| Sub-fase | LOC | Límite fase |
|---|---|---|
| E1-A (probes + report) | ~120 | ≤ 200 ✓ (cuenta como fase propia: "0.6-experimental") |
| E1-B (corte + V1-E + policy) | ~140 | ≤ 200 ✓ (Fase 1 primer peldaño) |
| Tests | ~80 | — |
| **Total nuevo** | **~340** | Proyecto: 1,029 + 185 (F0.5) + 340 = **1,554 < 2,000** ✓ (umbral R5) |

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| Acuerdo bajo (<95% en toda capa) | E1-A lo revela en ~1h de cómputo; se cierra el experimento con resultado negativo documentado (también es ciencia) |
| Overhead de probes contamina tok/s | probe-every M + pareja on/off; reporte separa "tok/s con probes" de "tok/s del modelo" |
| Loop manual diverge de HF en algún detalle (mask dtype, RoPE base) | Gate §5.1: sin cortes debe reproducir byte-exacto la traza monolítica |
| τ sobreajustado a los 5 prompts | holdout por prompt; τ reportado con su cobertura/precisión, no solo su valor |
| Tentación de activarlo por defecto | El flag es experimental; requiere ADR-0062 + resultado de calidad positivo |

## 8. Entregables

1. Curvas de acuerdo + tablas τ* (E1-A)
2. Informe frontera ΔPPL vs bytes/tok-s (E1-B)
3. `V1-E` generalizado en checks.py (dinámico por exit_layer) — reusable por el Simulator
4. Recomendación de política + integración Fase 1 (si capas tardías son frecuentemente saltadas → candidatas a residencia diferida: conexión directa con el Simulador)
5. ADR-0062 + aprendizajes en `ai-notes/AI_LEARNING.md`

## 9. Qué E1 NO es

- No es speculative decoding (eso genera con un draft y verifica — otra palanca, otra fase).
- No es destilación ni entrenamiento (τ es runtime).
- No compite con Fase 0.5 ni con el model loader: se agenda después; E1-A es ~1 sesión de implementación + cómputo corto (0.6B).
