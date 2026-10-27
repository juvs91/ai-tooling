"""HLWSS Observer — observación de accesos a tensores durante inferencia LLM.

Fase 0 (PyTorch/MPS): hooks por capa, Block Registry desde config.json,
KV derivado, eventos canónicos PRD §2.1 en Parquet + metadata §2.5,
fidelity checks V1–V4, análisis con DuckDB embebido.
"""

from .events import Event
from .observer import Observer, RunConfig, Trace
from .registry import BlockRegistry, BlockMeta

__all__ = ["Event", "Observer", "RunConfig", "Trace", "BlockRegistry", "BlockMeta"]
