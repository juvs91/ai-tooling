"""HLWSS Observer — Schema de eventos v0.4 (PRD §2.1).

Un Event registra una acción sobre un tensor (o bloque) durante la inferencia.
Los 11 campos son canónicos; __post_init__ valida los enums para fallar
rápido ante emisores mal instrumentados.
"""

from dataclasses import dataclass, fields

PHASES = frozenset({"prefill", "decode", "request"})
COMPONENTS = frozenset({"ATTN", "FFN", "EXPERT", "KV", "EMBED", "LM_HEAD",
                        "PLE", "AUG", "OTHER"})
EVENT_TYPES = frozenset({
    "EXECUTE", "LOGICAL_ACCESS", "PHYSICAL_TRANSFER",
    "PREFETCH", "EVICT", "SKIP",
})
SOURCES = frozenset({
    "hook", "profiler", "server_metric", "os_metric", "derived", "simulated",
})


@dataclass(frozen=True, slots=True)
class Event:
    timestamp_ns: int        # time.monotonic_ns() — único reloj del Observer
    request_id: str          # uuid4 por corrida; debe ser igual a metadata.run_id
    token_pos: int | None    # posición absoluta en decode; None en prefill
    phase: str               # "prefill" | "decode" | "request"
    layer: int | None
    component: str           # ATTN | FFN | EXPERT | KV | EMBED | LM_HEAD | OTHER
    block_id: str | None     # convención del Block Registry (PRD §2.2)
    event_type: str          # EXECUTE | LOGICAL_ACCESS | PHYSICAL_TRANSFER | PREFETCH | EVICT | SKIP
    size_bytes: int | None   # bytes REALMENTE tocados por este evento (PRD §2.3)
    duration_ns: int | None  # CPU wall del hook; NUNCA tiempo de device (MPS async)
    source: str              # hook | profiler | server_metric | os_metric | derived | simulated

    def __post_init__(self) -> None:
        if self.phase not in PHASES:
            raise ValueError(f"phase inválido: {self.phase!r}")
        if self.component not in COMPONENTS:
            raise ValueError(f"component inválido: {self.component!r}")
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"event_type inválido: {self.event_type!r}")
        if self.source not in SOURCES:
            raise ValueError(f"source inválido: {self.source!r}")
        if self.phase == "decode" and self.token_pos is None:
            raise ValueError("decode requiere token_pos (None solo en prefill)")

    def to_row(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}
