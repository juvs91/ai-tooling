# ADR-0070: Fase 4 — W_sat como propiedad del modelo + flag --temperature

Fecha: 2026-10-26 · Estado: propuesto · Branch: ex-observer-llm

## Contexto

Fase 3 (`ai-notes/findings/fase3-horizonte-largo.md`) refutó la predicción del
valle desplazado en tokens: W(t) satura en 7253MB antes de t=64 y no crece
más. El hallazgo con valor independiente que sobrevive es **W_sat** (working
set único tocado) como propiedad medible del modelo/prompt/modo — predice la
frontera de utilidad de cualquier política de residencia (tesis §3).

Para caracterizar W_sat hacen falta variar el *modo* de generación. El engine
solo soportaba greedy (argmax manual en el loop de decode); el eje
sampling-vs-greedy es el que más diversidad de routing puede inyectar.

## Decisión

1. `run_engine` acepta `temperature: float = 0.0`. Si > 0, el siguiente token
   se muestrea (`multinomial(softmax(logits/T))`) con `torch.manual_seed(42)`
   antes de la generación; 0.0 preserva el argmax actual (default idéntico).
2. CLI `engine run --temperature` (default 0.0).
3. Estudio Fase 4: W_sat de OLMoE en {greedy p0, sampling T=1 p0, greedy p1,
   Qwen1.5-MoE greedy p0} + replicación en caché fría (flush por presión) del
   contraste b=6 evict-vs-lru de Fase 2.

## Consecuencias

- Coste: ~6 LOC (golf compensado; cap 2900 se mantiene).
- El eje sampling también habilita futuros estudios de localidad bajo
  distribución real (no solo greedy repetitivo).
- No cambia defaults: corridas previas reproducibles bit a bit.
