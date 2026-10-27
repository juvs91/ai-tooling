"""Observer core — reloj, buffer, sampling determinístico y capture context.

Único reloj: time.monotonic_ns(). Todo en RAM hasta __exit__; el volcado
lo hace writer.py. El sampling 1-de-N es determinístico por token_pos
(PRD §2.6) y solo se activa si V3 supera el presupuesto de overhead.
"""

import time
import uuid
from dataclasses import dataclass, field

from .events import Event


@dataclass(frozen=True, slots=True)
class RunConfig:
    model_id: str
    prompts_file: str
    prompt_hash: str
    seed: int = 42
    temperature: float = 0.0
    do_sample: bool = False
    max_tokens: int = 256
    device: str = "cpu"          # "mps" | "cuda" | "cpu"
    sample_n: int = 1            # 1 = sin muestreo (V1/V4 exigen N=1)
    capture_distributions: bool = False
    kv_page_tokens: int = 16


@dataclass(slots=True)
class Trace:
    run_id: str
    cfg: RunConfig
    events: list[Event] = field(default_factory=list)
    dists: list[tuple] = field(default_factory=list)          # DistRow: (token_pos, layer, component, metric, value)
    router_logits: list[tuple] = field(default_factory=list)  # (token_pos, tensor) para V2


class Observer:
    def __init__(self) -> None:
        self._trace: Trace | None = None

    def capture(self, cfg: RunConfig) -> "Observer":
        if self._trace is not None:
            raise RuntimeError("capture() anidado no permitido")
        self._trace = Trace(run_id=str(uuid.uuid4()), cfg=cfg)
        return self

    def __enter__(self) -> Trace:
        if self._trace is None:
            raise RuntimeError("usar: observer.capture(cfg) como context manager")
        return self._trace

    def __exit__(self, *exc) -> None:
        self._trace = None  # el volcado lo hace writer.write(trace, ...)

    @property
    def trace(self) -> Trace:
        if self._trace is None:
            raise RuntimeError("fuera de capture()")
        return self._trace

    def _passes_sampling(self, token_pos: int | None) -> bool:
        n = self._trace.cfg.sample_n
        if n <= 1 or token_pos is None:
            return True
        return token_pos % n == 0

    def emit(self, e: Event) -> None:
        if self._trace is not None and self._passes_sampling(e.token_pos):
            self._trace.events.append(e)

    def now_ns(self) -> int:
        return time.monotonic_ns()

    def dist(self, token_pos: int, layer: int | None, component: str,
             metric: str, value: float) -> None:
        if self._trace is not None and self._trace.cfg.capture_distributions:
            self._trace.dists.append((token_pos, layer, component, metric, float(value)))
