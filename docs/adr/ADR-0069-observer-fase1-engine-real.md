# ADR-0069: Fase 1 — engine real de residencia (mmap + madvise + page-fault measurement)

- **Estado:** aceptado (staged 2026-10-25, branch `ex-observer-llm`)
- **Contexto:** ADR-0065 (simulador Fase 0.7), ADR-0066 (preflight política
  adaptativa), ADR-0064 (cap LOC R5 = 2,500)
- **Decisión:** construir `hlwss/engine.py`: inferencia real en CPU sobre
  pesos mmap (safetensors zero-copy), budget de RAM impuesto por buffer de
  presión, y medición de pageins reales (`ru_majflt`) para validar en
  hardware el valle de spill y el acantilado que el simulador predijo.
- **Bump de cap LOC:** 2,500 → **2,900** (mismo mecanismo que ADR-0064).

## Contexto y justificación

Toda la evidencia del proyecto (valle de spill MoE en Qwen1.5 y OLMoE,
acantilado denso en gemma3n, G2 cuantizar-vs-política) proviene del
simulador a granularidad de bloque. Ningún resultado fue validado en
hardware real. ADR-0066 dejó Fase 1 anunciada ("loader memmap") con la
justificación medida del preflight (wins de hlwss-adaptive en sim).

Este ADR fija el diseño:

1. **CPU sobre mmap, no MPS.** `safetensors.safe_open(framework="pt",
   device="cpu")` devuelve storages zero-copy sobre el mmap; tocarlos
   genera pageins file-backed contables. Mover a MPS copiaría los pesos a
   memoria anónima residente y destruiría el file-backing. CPU es además
   el escenario honesto del usuario de dispositivo con RAM limitada que
   motiva el proyecto. Verificado en spike: `ru_majflt` sube ~12.6k al
   tocar un tensor mmap (page size 16384 en M2).
2. **Budget por buffer de presión.** macOS no tiene cgroup/RLIMIT_AS
   útil; se reserva `torch.empty(...).fill_(1)` de tamaño
   `ram_total − budget − slack` (slack medido en calibración) para forzar
   reclaim del working set del modelo. budget_eff se reporta con slack
   real. Muerte por presión = fallo registrado, no ocultado.
3. **Medición.** `bytes_paged_in = Δru_majflt × page_size` por token;
   swap vía `sysctl vm.swapusage` para estimar file_bytes (caveat E5:
   ru_majflt cuenta swap-ins del buffer de presión también). Residencia
   por bloque vía `mincore` (ctypes) cada token → timeline de residencia
   por familia y atribución de faults (diff de bitmaps) — instrumentación
   de exploración para aprender del hardware, no solo validar.
4. **Política online.** `os-lru` (no-op) vs `hlwss-pin` (pin por
   frecuencia running, warmup 8 tokens, shrink-to-fit a 0.9×budget con
   las constantes de `simulate.py`; WILLNEED al tocar; DONTNEED al salir
   del conjunto residente; FIT a priori = registry WS ≤ budget → no-op,
   cierra el caveat #1 de ADR-0066 con señal conocida, no hindsight).
   Estado de la política dump por token (JSONL) para políticas aprendidas
   futuras.
5. **Alcance: pesos solamente.** El KV real del runtime es memoria
   anónima y no participa del paging de pesos; se documenta, no se simula.

## Consecuencias

- `hlwss engine run --model X --budget-gb B --policy P --max-tokens N`
  escribe `traces-engine/<run_id>/{report.json,residency.jsonl,
  policy.jsonl}`.
- LOC estimado: +~280 engine, +~80 tests, +~20 cli ≈ 2,810/2,900.
- Sujetos: OLMoE-1B-7B (principal, valle [8,11]GB sim), gemma-3n-E2B
  (cross-check denso), Qwen3-0.6B (varianza n=5, barato), Qwen1.5-MoE
  base (estiramiento, 32 tokens).
- Riesgos declarados: swap extremo (mitigación: budgets ≥6GB, monitoreo),
  madvise ignorado bajo baja presión (no-op benigno), jetsam (registrado
  como fallo de condición).
