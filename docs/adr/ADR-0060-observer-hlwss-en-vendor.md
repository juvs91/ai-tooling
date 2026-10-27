# ADR-0060: Observer HLWSS como proyecto propio en vendor/hlwss/

- **Date**: 2026-09-29
- **Status**: Accepted
- **Deciders**: Jorge Guzman

---

## Context

El proyecto HLWSS (ver `docs/prds/observer-llm/prd-v.0.4.md`, aprobado para desarrollo) construye un **Observer** de LLMs: instrumentación que registra cada acceso a tensor (por capa, por token, en prefill y decode) durante la inferencia, con el objetivo de entender el uso real de memoria RAM↔SSD (spill) y alimentar un Simulator de políticas de residencia. Fase 0 = Observer PyTorch sobre Apple Silicon 12 GB (modelo Qwen3-0.6B), validado por 4 fidelity checks (V1 bytes, V2 router MoE, V3 overhead ≤5%, V4 reproducibilidad).

Decisiones de ubicación consideradas:

1. **Carpeta top-level propia** (`hlwss/`): máxima visibilidad, pero ai-tooling ya concentra proyectos completos bajo `vendor/` (claude-code-proxy, ralph) con el patrón "proyecto autocontenido con su propio venv".
2. **`tools/hlwss/`**: `tools/` está orientado a utilidades standalone del workflow, no a un proyecto con modelo, trazas y venv.
3. **`vendor/hlwss/`**: consistente con el patrón existente de proyectos autocontenidos; venv por proyecto aísla ~2-3 GB de deps (torch, transformers) del resto del monorepo.
4. **Repo git independiente**: fragmenta el ciclo de vida (ADRs, hooks, templates viven en ai-tooling).

Nota sobre el ADR-gate: `vendor/hlwss/` NO está en los guarded prefixes de `.claude/hooks/adr-gate.sh` (solo `vendor/claude-code-proxy/`), por lo que este ADR es documentación de decisión, no requisito del hook.

## Decision

1. **El Observer HLWSS vive en `vendor/hlwss/`** como proyecto autocontenido: paquete `hlwss/`, `pyproject.toml` propio, venv dedicado (`vendor/hlwss/.venv`), `prompts/`, `tests/`, `traces/` (gitignored).
2. **Fase 0 = PyTorch + MPS** con modelo Qwen3-0.6B (bf16): el modelo más grande que corre cómodo en 12 GB como tensors bf16 y permite observación por capa completa (ground truth). Los modelos grandes vía Ollama/GGUF son objeto de Fase 0.5 (spill curve), no de Fase 0.
3. **Venv por proyecto, no en root** (requisito multi-app del monorepo): cada aplicación futura tendrá su propio entorno; torch/transformers no contaminan el root.
4. **Contrato "wrap, don't rebuild"** (PRD §10): el Observer envuelve el forward del modelo con hooks, nunca reimplementa el engine. La instrumentación es agnóstica al modelo: todo se lee del `config.json` real (hidden, vocab, tie_word_embeddings, sliding_window, n_kv_heads, head_dim, campos MoE) — prohibido hardcodear.
5. **Motor de análisis = DuckDB embebido** sobre Parquet (sin servidor, sin NoSQL); pandas solo para reportes.
6. **Decisiones de Fase 1 registradas**: engine instrumentado = **PyTorch portable** (MPS en Mac, CUDA en Linux, mismo código — requisito de portabilidad Mac↔Linux del propietario). MLX directo queda descartado como backend Fase 1 (Mac-only contradice la portabilidad); oMLX queda como referencia de diseño para tiered KV. llama.cpp/Ollama = línea base de producción portable, observada solo por fuera (caja negra: sin hooks de tensor).

## Consequences

**Positive:**
- Patrón consistente con los proyectos existentes en `vendor/`; venv y trazas aislados del resto del monorepo.
- El código del Observer de Fase 0 crece naturalmente hacia el engine instrumentado portable de Fase 1 (memmap de safetensors + políticas de residencia) sin reescritura.
- Los fidelity checks V1–V4 dan una red de seguridad cuantitativa contra regresiones de fidelidad.

**Negative:**
- `vendor/` acumula un tercer proyecto; el índice del repo debería listarlo (README).
- torch+transformers (~2-3 GB) por proyecto: aceptado, es el costo del aislamiento multi-app.

**Neutral:**
- El plan de implementación detallado vive en `.claude/plans/observer-llm-fase-0.md`; los aprendizajes de sesión van a `ai-notes/AI_LEARNING.md` (Feedback Loop).
