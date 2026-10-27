# Experimento: mecanismo de la "ley num_ctx/2" de Ollama

**Fecha:** 2026-10-02 · **Rama:** ex-observer-llm · **Estado:** resuelto (mecanismo acotado, causa raíz pendiente de source-diving)

## Pregunta

Fase 0.5 descubrió empíricamente que Ollama entrega ~`num_ctx/2 + 2` tokens de prompt
útiles (2048→1026, 8192→4098, 131072→65538). ¿De dónde sale la mitad perdida?
Hipótesis candidata: **reserva para la salida** (espacio de generación).

## Método (experimento discriminador)

Envío un prompt deliberadamente mayor que cualquier ventana (3× filler ≈ 53k tokens)
para forzar truncado, y leo `prompt_eval_count` del mensaje final de
`/api/generate` — ese contador es el borde usable exacto. Varío `num_predict`
con `num_ctx` fijo: si el borde se mueve con `num_predict` → reserva de salida;
si no → partición fija de otra naturaleza.

Modelo: `qwen2.5-coder:7b` (FIT, no interfiere memoria). Temperatura 0.

## Resultados

| num_ctx | num_predict | prompt_eval_count | eval_count | done_reason |
|---|---|---|---|---|
| 8192 | 64 | **4098** | 64 | length |
| 8192 | 4000 | **4098** | 1139 | stop |
| 16384 | 64 | **8194** | 64 | length |

## Conclusión

1. **Descartada la reserva para salida:** con `num_predict=4000` el borde quedó
   idéntico (4098). Ollama da la mitad al prompt **sin importar cuánto vayas a
   generar**.
2. **Es una partición fija 50/50 del lado del servidor:** `usable = num_ctx/2 + 2`
   exacto en los tres puntos (4098 = 4096+2; 8194 = 8192+2). El `+2` probablemente
   son tokens especiales (BOS + separador), a confirmar.
3. Es **config de Ollama/runner, no del modelo** (qwen soporta 262k nativo).
4. Dato extra incidental: en la corrida B el modelo generó 1139 tokens y paró solo
   (`stop`), o sea 4098+1139 = 5237 < 8192 — **la generación no está limitada por
   la mitad que quedó "reservada"**, lo que vuelve aún más extraña la partición.
   Sospecha: el runner de Ollama 0.34.x (llama-server) recibe `--ctx-size num_ctx/2`
   o reparte la ventana en dos slots/regiones. Requiere source-diving para
   confirmar (follow-up).

## Implicaciones (ya aplicadas)

- PRD §4 ya codifica la regla práctica: **para N tokens reales de contexto,
  pedir `num_ctx = 2N`** (Caso 0 usó 131072 para lograr 64k reales).
- Cualquier futura medición de "contexto máximo usable" debe leer
  `prompt_eval_count`, no asumir que num_ctx es el límite.

## Reproducir

```bash
cd vendor/hlwss && .venv/bin/python - <<'EOF'
import sys; sys.path.insert(0, ".")
from hlwss.ollama_probe import http_json
prompt = open("prompts/context-filler.txt").read() * 3
r = http_json("/api/generate", {"model": "qwen2.5-coder:7b",
    "prompt": prompt, "stream": False,
    "options": {"num_ctx": 8192, "num_predict": 64, "temperature": 0}})
print(r["prompt_eval_count"])  # → 4098
EOF
```

## Qué NO se revisó

- Source code de Ollama 0.34.x / llama.cpp server (causa raíz exacta de la
  partición 50/50 — candidato: división de ventana en el runner, o default de
  slots por GPU/partición KV).
- Comportamiento con `num_keep`, template de chat, o modelos con
  `max_position_embeddings` menor que num_ctx.
- Si la partición cambia entre versiones de Ollama.
