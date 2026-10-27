"""Adapter PyTorch — hooks por módulo, router MoE, KV derivado y loop de
generación manual (sin generate() de HF: control total del reloj y del greedy).

Reglas de emisión (PRD §2.3): por bloque poseído, 1 EXECUTE + 1 LOGICAL_ACCESS
con el mismo timestamp base; size_bytes = bytes REALMENTE tocados (embed = una
fila, nunca el bloque completo). duration_ns = CPU wall del par pre/post hook
(nunca tiempo de device — MPS es asíncrono). Con HLWSS_OFF=1 no se registra
ningún hook (passthrough puro para V3).

MoE (V2): los expertos seleccionados los captura el hook de cada experto
(solo se ejecutan los elegidos). El hook del router NO emite eventos: solo
guarda (token_pos, logits, selección) para que V2 compare
experto-ejecutado == topk(router_logits).
"""

import os

import torch

from .events import Event
from .observer import Observer
from .registry import BlockRegistry, head_dim

EXEC_AND_ACCESS = ("EXECUTE", "LOGICAL_ACCESS")


def kv_bytes_per_step(cfg, dtype_bytes: int, layer: int, context_tokens: int,
                      kind: str | None = None) -> int:
    """Bytes de KV leídos por la atención en un paso con contexto de
    `context_tokens` tokens (PRD §2.4). `kind` = 'full' | 'swa:{W}' |
    None (legacy: usa cfg.sliding_window global)."""
    n_kv = cfg.num_key_value_heads
    if kind is None:
        w = getattr(cfg, "sliding_window", None)
    elif kind.startswith("swa:"):
        w = int(kind.split(":", 1)[1])
    else:
        w = None  # full attention: sin tope de ventana
    eff = min(context_tokens, w) if w else context_tokens
    return 2 * n_kv * head_dim(cfg) * dtype_bytes * eff


def _layer_of(name: str) -> int | None:
    parts = name.split(".")
    return int(parts[parts.index("layers") + 1]) if "layers" in parts else None


class Adapter:
    def __init__(self, model: torch.nn.Module, registry: BlockRegistry,
                 observer: Observer):
        self.model = model
        self.registry = registry
        self.observer = observer
        self.dtype_bytes = model.dtype.itemsize
        self._handles: list = []
        self._t0: dict = {}
        self._module_bid: dict = {}
        self._fused_experts = False
        self._phase = "prefill"
        self._token_pos: int | None = None

    # ---- instrumentación ----------------------------------------------

    def instrument(self) -> None:
        if os.environ.get("HLWSS_OFF") == "1":
            return
        saw_expert_module = False
        for name, module in self.model.named_modules():
            bid = self.registry.block_id_for_module(name)
            if bid is not None and ".expert." in bid:
                saw_expert_module = True
            if bid is None:
                continue
            if bid == "router":
                layer = _layer_of(name)
                self._handles.append(
                    module.register_forward_hook(self._router_hook(layer)))
            else:
                self._module_bid[module] = bid
                self._handles.append(module.register_forward_pre_hook(self._pre_hook))
                self._handles.append(
                    module.register_forward_hook(self._post_hook(module)))
        # transformers 5.x fusiona los expertos en un solo módulo
        # (mlp.experts sin submódulos por experto): no hay hook por experto,
        # así que la selección la emite el router (PRD §2.3 regla 3).
        self._fused_experts = not saw_expert_module

    def remove(self) -> None:
        for h in self._handles:
            h.remove()
        self._handles.clear()

    # ---- hooks ----------------------------------------------------------

    def _pre_hook(self, module, args) -> None:
        self._t0[module] = self.observer.now_ns()

    def _post_hook(self, module):
        def hook(mod, args, output):
            bid = self._module_bid[mod]
            t0 = self._t0.pop(mod, None)
            dur = self.observer.now_ns() - t0 if t0 is not None else None
            ts = self.observer.now_ns()
            cfg = self.registry.cfg  # text config (gemma3n viene desenvuelto)
            if bid == "ple":
                # Gemma 3n (ADR-0067): el módulo único embed_tokens_per_layer
                # sirve las 35 tablas en un solo forward; se emite un acceso
                # por capa a tamaño de FILA (n_tokens × ple_dim × dtype)
                n_tok = int(args[0].numel()) if args and torch.is_tensor(args[0]) else 1
                size = n_tok * cfg.hidden_size_per_layer_input * self.dtype_bytes
                metas = self.registry.blocks()
                for i in range(cfg.num_hidden_layers):
                    meta = metas[f"L{i}.ple"]
                    for et in EXEC_AND_ACCESS:
                        self.observer.emit(Event(
                            ts, self.observer.trace.run_id, self._token_pos,
                            self._phase, i, "PLE", f"L{i}.ple", et, size, dur,
                            "hook"))
                return
            meta = self.registry.blocks()[bid]
            if bid == "embed":
                size = cfg.hidden_size * self.dtype_bytes  # una fila (§2.3)
            else:
                size = self.registry.touch_size(bid)
            for et in EXEC_AND_ACCESS:
                self.observer.emit(Event(
                    ts, self.observer.trace.run_id, self._token_pos, self._phase,
                    meta.layer, meta.component, bid, et, size, dur, "hook"))
            cfg = self.observer.trace.cfg
            if cfg.capture_distributions and self._token_pos is not None and bid != "embed":
                out = output[0] if isinstance(output, tuple) else output
                if torch.is_tensor(out):
                    self.observer.dist(self._token_pos, meta.layer,
                                       meta.component, "out_l2_norm",
                                       float(out.float().norm()))
        return hook

    def _router_hook(self, layer: int | None):
        def hook(mod, args, output):
            logits = output[0] if isinstance(output, tuple) else output
            logits = logits.detach()
            k = getattr(self.model.config, "num_experts_per_tok", 2)
            sel = logits.topk(k, dim=-1).indices.flatten().tolist()
            if self._fused_experts and layer is not None:
                ts = self.observer.now_ns()
                for j in sel:
                    bid = f"L{layer}.expert.{j}"
                    meta = self.registry.blocks().get(bid)
                    if meta is None:
                        continue
                    for et in EXEC_AND_ACCESS:
                        self.observer.emit(Event(
                            ts, self.observer.trace.run_id, self._token_pos,
                            self._phase, layer, "EXPERT", bid, et,
                            meta.size_bytes, None, "hook"))
            self.observer.trace.router_logits.append(
                (self._token_pos, self._phase, layer, logits.cpu(), sel))
        return hook

    # ---- generación manual ----------------------------------------------

    @torch.no_grad()
    def run(self, prompt_ids: list[int], max_tokens: int) -> list[int]:
        """Prefill (forward único, token_pos=None) + loop greedy de decode con
        KV cache (use_cache): cada paso alimenta solo el último token, con la
        semántica de producción que el KV derivado (§2.4) modela.
        token_pos = índice del token siendo generado (gen[0] sale del prefill,
        pero por schema sus eventos llevan phase=prefill, token_pos=None)."""
        self.model.eval()
        device = next(self.model.parameters()).device
        cfg = self.registry.cfg  # text config: Qwen directo, gemma3n desenvuelto
        eos = getattr(cfg, "eos_token_id", None)
        if isinstance(eos, (list, tuple)):
            eos_ids = set(eos)
        else:
            eos_ids = {eos} if eos is not None else set()
        self._phase, self._token_pos = "prefill", None
        ids = torch.tensor([prompt_ids], device=device)
        out = self.model(input_ids=ids, use_cache=True)
        past = out.past_key_values
        self._emit_kv(len(prompt_ids))
        self._dist_logits(out.logits[0, -1])
        nxt = int(out.logits[0, -1].argmax())
        gen = [nxt]
        for t in range(1, max_tokens):
            if nxt in eos_ids:
                break
            self._phase, self._token_pos = "decode", t
            step = torch.tensor([[nxt]], device=device)
            out = self.model(input_ids=step, past_key_values=past, use_cache=True)
            past = out.past_key_values
            self._emit_kv(len(prompt_ids) + t)
            self._dist_logits(out.logits[0, -1])
            nxt = int(out.logits[0, -1].argmax())
            gen.append(nxt)
        return gen

    def _emit_kv(self, context_tokens: int) -> None:
        ts = self.observer.now_ns()
        cfg = self.registry.cfg  # text config (Qwen directo, gemma3n desenvuelto)
        kinds = self.registry.kv_model_layers()
        for i in range(cfg.num_hidden_layers):
            size = kv_bytes_per_step(cfg, self.dtype_bytes, i, context_tokens,
                                     kinds.get(i))
            self.observer.emit(Event(
                ts, self.observer.trace.run_id, self._token_pos, self._phase,
                i, "KV", f"L{i}.kv", "LOGICAL_ACCESS", size, None, "derived"))

    def _dist_logits(self, logits_row: torch.Tensor) -> None:
        if not self.observer.trace.cfg.capture_distributions or self._token_pos is None:
            return
        t = self._token_pos
        probs = torch.softmax(logits_row.float(), dim=-1)
        top = probs.topk(min(5, probs.numel()))
        self.observer.dist(t, None, "LM_HEAD", "entropy",
                           float(-(probs * probs.log().clamp_min(-1e9)).sum()))
        self.observer.dist(t, None, "LM_HEAD", "top1_prob", float(top.values[0]))
        self.observer.dist(t, None, "LM_HEAD", "top5_prob_mass", float(top.values.sum()))
