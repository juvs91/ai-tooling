"""Block Registry (PRD §2.2) — unidad de residencia/transferencia del sistema.

Todo se deriva del config.json real del modelo (nada hardcodeado, PRD §3).
Bloques: embed, L{i}.attn, L{i}.ffn, lm_head, L{i}.kv (+ L{i}.expert.{j},
L{i}.shared_expert en MoE). Normas/rotary/biases FUERA del Registry (<1% bytes).

Sidecar compacto: las páginas de KV no se materializan (serían miles de
entradas); va una plantilla por capa y kv_pages() las genera bajo demanda.
"""

from dataclasses import dataclass

KV_PAGE_TOKENS_DEFAULT = 16


@dataclass(frozen=True, slots=True)
class BlockMeta:
    layer: int | None
    component: str
    size_bytes: int          # vista de almacenamiento (dedup de tied aplicado)
    tied_with: str | None = None
    aggregated: bool = False  # L{i}.kv agregado: unidad de emisión, no de residencia
    derived: bool = False     # tamaño calculado por fórmula, no medido


def head_dim(cfg) -> int:
    hd = getattr(cfg, "head_dim", None)
    if hd:
        return int(hd)
    return cfg.hidden_size // cfg.num_attention_heads


def context_window(cfg) -> int:
    return int(getattr(cfg, "max_position_embeddings", 4096))


def is_moe(cfg) -> bool:
    return bool(getattr(cfg, "num_experts", 0))


def is_gemma3n(cfg) -> bool:
    return getattr(cfg, "model_type", "") == "gemma3n"


class BlockRegistry:
    def __init__(self, cfg, dtype_bytes: int, kv_page_tokens: int = KV_PAGE_TOKENS_DEFAULT,
                 expert_dtype_bytes: float | None = None):
        self._gemma3n = is_gemma3n(cfg)
        if self._gemma3n:
            cfg = cfg.text_config  # Gemma3nConfig envuelve el text config
        self.cfg = cfg
        self.dtype_bytes = dtype_bytes
        # bytes por peso de los EXPERTOS puede diferir del resto cuando el
        # layout es mixto (p.ej. MoE con expertos int4 y resto bf16, ADR-0063)
        self.expert_dtype_bytes = dtype_bytes if expert_dtype_bytes is None \
            else expert_dtype_bytes
        self.kv_page_tokens = kv_page_tokens
        self._blocks: dict[str, BlockMeta] = {}
        self._build()

    # ---- construcción -------------------------------------------------

    def _build(self) -> None:
        if self._gemma3n:
            self._build_gemma3n()
            return
        cfg, d = self.cfg, self.dtype_bytes
        h, vocab = cfg.hidden_size, cfg.vocab_size
        n_layers = cfg.num_hidden_layers
        self._blocks["embed"] = BlockMeta(None, "EMBED", vocab * h * d)
        if getattr(cfg, "tie_word_embeddings", False):
            self._blocks["lm_head"] = BlockMeta(None, "LM_HEAD", 0, tied_with="embed")
        else:
            self._blocks["lm_head"] = BlockMeta(None, "LM_HEAD", vocab * h * d)
        for i in range(n_layers):
            self._blocks[f"L{i}.attn"] = BlockMeta(i, "ATTN", self._attn_bytes())
            self._blocks[f"L{i}.ffn"] = BlockMeta(i, "FFN", self._ffn_bytes())
            self._blocks[f"L{i}.kv"] = BlockMeta(
                i, "KV", self.kv_page_bytes() * self._n_pages(context_window(cfg)),
                aggregated=True, derived=True,
            )
            if is_moe(cfg):
                moe_inter = getattr(cfg, "moe_intermediate_size", cfg.intermediate_size)
                de = self.expert_dtype_bytes
                for j in range(cfg.num_experts):
                    self._blocks[f"L{i}.expert.{j}"] = BlockMeta(
                        i, "EXPERT", 3 * h * moe_inter * de)
                if getattr(cfg, "num_experts_per_tok", 0):
                    shared = getattr(cfg, "shared_expert_intermediate_size", None)
                    if shared:
                        self._blocks[f"L{i}.shared_expert"] = BlockMeta(
                            i, "EXPERT", 3 * h * shared * self.dtype_bytes)

    def _build_gemma3n(self) -> None:
        """Gemma 3n (ADR-0067): PLE, aug (laurel/altup/gates), atención
        híbrida y capas superiores SIN k/v (KV compartida). Sin towers
        vision/audio (G1 texto-only)."""
        cfg, d = self.cfg, self.dtype_bytes
        h, vocab = cfg.hidden_size, cfg.vocab_size
        n_layers = cfg.num_hidden_layers
        inter = cfg.intermediate_size  # lista por capa (MatFormer)
        if not isinstance(inter, (list, tuple)):
            inter = [inter] * n_layers
        ple_vocab = getattr(cfg, "vocab_size_per_layer_input", vocab)
        ple_dim = getattr(cfg, "hidden_size_per_layer_input", 0)
        n_shared = getattr(cfg, "num_kv_shared_layers", 0)
        first_shared = n_layers - n_shared
        self._blocks["embed"] = BlockMeta(None, "EMBED", vocab * h * d)
        if getattr(cfg, "tie_word_embeddings", False):
            self._blocks["lm_head"] = BlockMeta(None, "LM_HEAD", 0, tied_with="embed")
        else:
            self._blocks["lm_head"] = BlockMeta(None, "LM_HEAD", vocab * h * d)
        for i in range(n_layers):
            self._blocks[f"L{i}.attn"] = BlockMeta(i, "ATTN", self._attn_bytes(i))
            self._blocks[f"L{i}.ffn"] = BlockMeta(i, "FFN", self._ffn_bytes(i))
            self._blocks[f"L{i}.aug"] = BlockMeta(i, "AUG", self._aug_bytes())
            self._blocks[f"L{i}.ple"] = BlockMeta(i, "PLE", ple_vocab * ple_dim * d)
            self._blocks[f"L{i}.kv"] = BlockMeta(
                i, "KV", self.kv_page_bytes() * self._n_pages(context_window(cfg)),
                aggregated=True, derived=True,
            )
        # proyección global de la vía PLE (model-level, una por modelo)
        self._blocks["ple.proj"] = BlockMeta(
            None, "PLE", h * n_layers * ple_dim * d)

    def _attn_bytes(self, layer: int | None = None) -> int:
        cfg, d = self.cfg, self.dtype_bytes
        h, hd = cfg.hidden_size, head_dim(cfg)
        qo = 2 * h * cfg.num_attention_heads * hd * d    # q + o
        if self._gemma3n and layer is not None:
            # capas con KV compartida no tienen k_proj/v_proj (ADR-0067):
            # las últimas num_kv_shared_layers capas solo leen q/o
            n_shared = getattr(cfg, "num_kv_shared_layers", 0)
            if layer >= cfg.num_hidden_layers - n_shared:
                return qo
        kv = 2 * h * cfg.num_key_value_heads * hd * d    # k + v
        return qo + kv

    def _ffn_bytes(self, layer: int | None = None) -> int:
        inter = self.cfg.intermediate_size
        if isinstance(inter, (list, tuple)):  # MatFormer: tamaño por capa
            inter = inter[layer]
        return 3 * self.cfg.hidden_size * inter * self.dtype_bytes

    def _aug_bytes(self) -> int:
        """Gemma 3n: laurel + altup + gates PLE por capa (ADR-0067).
        Fórmula validada contra parámetros reales del checkpoint E2B
        (exacta a los 48 parámetros de normas pequeñas):
        laurel = 2·h·rank + h (post_laurel_norm);
        altup  = h·n + n³ + n² + 2h (modality_router + prediction_coefs
                 [n×n²] + correction_coefs [n×n] + router_norm + scale);
        gates  = 2·h·ple_dim (input_gate + per_layer_projection)."""
        cfg, d = self.cfg, self.dtype_bytes
        h = cfg.hidden_size
        ple_dim = getattr(cfg, "hidden_size_per_layer_input", 0)
        rank = getattr(cfg, "laurel_rank", 0)
        n_in = getattr(cfg, "altup_num_inputs", 0)
        laurel = 2 * h * rank + h
        altup = h * n_in + n_in**3 + n_in**2 + 2 * h
        gates = 2 * h * ple_dim
        return (laurel + altup + gates) * d

    def _n_pages(self, upto_token: int) -> int:
        return -(-int(upto_token) // self.kv_page_tokens)  # ceil

    # ---- consultas ----------------------------------------------------

    def blocks(self) -> dict[str, BlockMeta]:
        return dict(self._blocks)

    def size(self, block_id: str) -> int:
        return self._blocks[block_id].size_bytes

    def touch_size(self, block_id: str) -> int:
        """Bytes realmente leídos al tocar el bloque: resuelve tied."""
        meta = self._blocks[block_id]
        if meta.tied_with:
            return self._blocks[meta.tied_with].size_bytes
        return meta.size_bytes

    def kv_page_bytes(self) -> int:
        cfg, d = self.cfg, self.dtype_bytes
        return 2 * cfg.num_key_value_heads * head_dim(cfg) * d * self.kv_page_tokens

    def kv_pages(self, layer: int, upto_token: int) -> list[str]:
        return [f"L{layer}.kv.p{j}" for j in range(self._n_pages(upto_token))]

    def kv_model_layers(self) -> dict[int, str]:
        """Mapa por capa: 'full' | 'swa:{W}' — cierra el hueco de atención
        híbrida intercalada (Mistral/Gemma) que un string global no cubre."""
        if self._gemma3n:
            w = getattr(self.cfg, "sliding_window", None)
            kinds = getattr(self.cfg, "layer_types", None) or []
            out = {}
            for i in range(self.cfg.num_hidden_layers):
                k = kinds[i] if i < len(kinds) else "sliding_attention"
                out[i] = "full" if k == "full_attention" else f"swa:{w}"
            return out
        w = getattr(self.cfg, "sliding_window", None)
        kind = "full" if not w else f"swa:{w}"
        return {i: kind for i in range(self.cfg.num_hidden_layers)}

    def block_id_for_module(self, name: str) -> str | None:
        """Convención de nombres Qwen3 (denso y MoE) y Gemma 3n (ADR-0067)
        → block_id del Registry."""
        if self._gemma3n:
            return self._block_id_gemma3n(name)
        if name == "model.embed_tokens":
            return "embed"
        if name == "lm_head" or name.endswith(".lm_head"):
            return "lm_head"
        parts = name.split(".")
        if "layers" not in parts:
            return None
        idx = parts.index("layers")
        if idx + 1 >= len(parts):
            return None  # el contenedor model.layers no posee bloque
        i = int(parts[idx + 1])
        tail = parts[idx + 2:]
        if tail == ["self_attn"]:
            return f"L{i}.attn"
        if tail[:1] == ["mlp"]:
            if len(tail) == 1:
                return None if is_moe(self.cfg) else f"L{i}.ffn"  # contenedor MoE
            if tail[1:2] == ["gate"]:
                return "router"  # no es bloque; lo usa el adapter para V2
            if tail[1:2] == ["shared_expert"]:
                # solo el CONTENEDOR posee el bloque: sus hijos (gate/up/down/
                # act) mapear a lo mismo y duplicarían ~5× por paso (mismo
                # bug que el FFN denso documentado arriba; apareció con
                # Qwen1.5-MoE, primer modelo con shared_expert real)
                return f"L{i}.shared_expert" if len(tail) == 2 else None
            if tail[1:2] == ["experts"] and len(tail) == 3 and tail[2].isdigit():
                # contenedor del experto individual (modelos no fusionados);
                # sus proyecciones internas (len>3) no poseen bloque
                return f"L{i}.expert.{int(tail[2])}"
            if tail[1:2] == ["experts"]:
                return None  # contenedor/fused-experts: sin bloque propio
            return None  # proyecciones densas (gate/up/down, act_fn): el
            # bloque lo posee el container mlp, no sus hijos — si cada
            # proyección mapeara a L{i}.ffn, el FFN se emitiría ~5× (bug
            # real detectado en Qwen3-0.6B: 5 LOGICAL_ACCESS por paso).
        return None

    def _block_id_gemma3n(self, name: str) -> str | None:
        """Gemma 3n (ADR-0067). 'ple' es un MARCADOR: el módulo
        embed_tokens_per_layer es único y el adapter hace fan-out a
        L{i}.ple por capa con tamaño de fila."""
        # Gemma3nForConditionalGeneration: model.language_model.X;
        # Gemma3nForCausalLM (text-only, sin dep timm): model.X
        if name.startswith("model.language_model."):
            name = "model." + name[len("model.language_model."):]
        prefix = "model."
        if name == prefix + "embed_tokens":
            return "embed"
        if name == prefix + "embed_tokens_per_layer":
            return "ple"
        if name == prefix + "per_layer_model_projection":
            return "ple.proj"
        if name == "lm_head":
            return "lm_head"
        if not name.startswith(prefix + "layers."):
            return None  # towers vision/audio y demás: sin bloque en G1
        rest = name[len(prefix) + len("layers."):]
        head, _, tail = rest.partition(".")
        if not head.isdigit():
            return None
        i = int(head)
        if tail == "self_attn":
            return f"L{i}.attn"
        if tail == "mlp":
            return f"L{i}.ffn"
        if tail == "laurel":
            # owner único de la familia aug (mismo patrón que mlp→L{i}.ffn):
            # altup se invoca vía .predict()/.correct() (métodos, sin hook) y
            # per_layer_input_gate/projection son hermanos; si cada uno
            # emitiera el bloque completo, el conteo saldría 3× inflado
            return f"L{i}.aug"
        if tail in ("altup", "per_layer_input_gate", "per_layer_projection"):
            return None  # sus pesos ya viven en el bloque de laurel
        return None  # norms/inner projections: sin bloque propio

    def sidecar(self) -> dict:
        """Sidecar JSON: bloques + plantilla compacta de páginas KV."""
        blocks = {bid: {"layer": m.layer, "component": m.component,
                        "size_bytes": m.size_bytes, "tied_with": m.tied_with,
                        **({"aggregated": True} if m.aggregated else {}),
                        **({"derived": True} if m.derived else {})}
                  for bid, m in self._blocks.items()}
        return {
            "n_layers": self.cfg.num_hidden_layers,
            "n_experts": int(getattr(self.cfg, "num_experts", 0)),
            "context_window": context_window(self.cfg),
            "dtype_bytes": self.dtype_bytes,
            "blocks": blocks,
            "kv_pages_template": {
                "kv_page_tokens": self.kv_page_tokens,
                "kv_page_bytes": self.kv_page_bytes(),
                "n_pages_for_context_window": self._n_pages(context_window(self.cfg)),
                "id_pattern": "L{i}.kv.p{j}",
            },
            "kv_model_layers": {str(k): v for k, v in self.kv_model_layers().items()},
        }

    @classmethod
    def from_config(cls, cfg, dtype_bytes: int,
                    kv_page_tokens: int = KV_PAGE_TOKENS_DEFAULT,
                    expert_dtype_bytes: float | None = None) -> "BlockRegistry":
        return cls(cfg, dtype_bytes, kv_page_tokens, expert_dtype_bytes)
