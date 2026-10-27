"""Early-exit probes (E1-A, ADR-0062) — logit-lens por capa, sin cortar.

Cada forward de una capa decodificadora se proyecta con lm_head(norm(h_l)) y se
compara el argmax contra el argmax final: si se estabiliza antes de la última
capa, las capas posteriores son pesos que no eran necesarios en la ruta crítica
de ese token. Solo se guardan estadísticos (argmax, entropía) — nunca
logits completos (28 capas × N tokens × 2 floats ≪ vocab 151k por fila).

Composición: el probe NO tiene loop propio. Son puros hooks que coexisten con el
Adapter (que sigue midiendo bytes con phase/token_pos correctos): una sola
corrida produce traza Fase 0 + estabilidad E1-A, insumo de V1-E (bytes
esperados dinámicos según capa de salida).
"""

import torch


class EarlyExitProbe:
    """Hooks post-capa + hook lm_head. Uso: attach() → adapter.run() → detach()
    → report()."""

    def __init__(self, model: torch.nn.Module):
        base = getattr(model, "model", None)
        if base is None or not hasattr(base, "layers") or not hasattr(base, "norm"):
            raise TypeError("modelo sin estructura .model.layers/.model.norm")
        self.model = model
        self.norm = base.norm
        self.lm_head = model.lm_head
        self.layers = list(base.layers)
        self.n_layers = len(self.layers)
        self.dtype_bytes = model.dtype.itemsize
        self._per_layer_argmax: list[list[int]] = [[] for _ in self.layers]
        self._per_layer_entropy: list[list[float]] = [[] for _ in self.layers]
        self.final_argmax: list[int] = []
        self.final_entropy: list[float] = []
        self._in_probe = False  # el lm_head hook ignora las llamadas propias
        self._handles: list = []

    # ---- hooks ----------------------------------------------------------

    def attach(self) -> None:
        for i, layer in enumerate(self.layers):
            self._handles.append(
                layer.register_forward_hook(self._make_layer_hook(i)))
        self._handles.append(
            self.lm_head.register_forward_hook(self._lm_head_hook))

    def detach(self) -> None:
        for h in self._handles:
            h.remove()
        self._handles.clear()

    def _stats(self, logits: torch.Tensor) -> tuple[int, float, float]:
        probs = torch.softmax(logits.float(), dim=-1)
        top = probs.topk(2)
        # clamp sobre las PROBS (no sobre el log): log(0)=-inf queda neutralizado
        # y las probs reales pasan intactas (clamp sobre el log las destruiría)
        entropy = float(-(probs * probs.clamp_min(1e-12).log()).sum())
        return int(logits.argmax()), entropy, float(top.values[0])

    def _make_layer_hook(self, i: int):
        def hook(mod, args, output):
            h = output[0] if isinstance(output, tuple) else output
            row = h[0, -1]  # última posición: en decode es el único token nuevo
            self._in_probe = True
            try:
                logits = self.lm_head(self.norm(row))
            finally:
                self._in_probe = False
            am, ent, _ = self._stats(logits)
            self._per_layer_argmax[i].append(am)
            self._per_layer_entropy[i].append(ent)
        return hook

    def _lm_head_hook(self, mod, args, output):
        if self._in_probe:
            return
        logits = output[0] if isinstance(output, tuple) else output
        am, ent, _ = self._stats(logits[0, -1])
        self.final_argmax.append(am)
        self.final_entropy.append(ent)

    # ---- reporte --------------------------------------------------------

    def _layer_bytes(self) -> list[int]:
        """Bytes de pesos por capa (uniforme en qwen3; se mide, no asume)."""
        return [sum(p.numel() for p in layer.parameters()) * self.dtype_bytes
                for layer in self.layers]

    def report(self, taus=(0.90, 0.95, 0.99)) -> dict:
        """Acuerdo por capa + primera capa con acuerdo ≥ τ + bytes ahorrables.

        Bytes ahorrados al salir EN la capa k = pesos de las capas k+1..n-1
        (ya no se leen por token) — el puente cuantitativo con la tesis de
        spill: menos bytes por token en la ruta crítica."""
        n_tok = len(self.final_argmax)
        if n_tok == 0:
            return {"n_tokens": 0, "n_layers": self.n_layers}
        agreement = [
            sum(1 for a, f in zip(self._per_layer_argmax[i], self.final_argmax)
                if a == f) / n_tok
            for i in range(self.n_layers)
        ]
        layer_bytes = self._layer_bytes()
        first, saved = {}, {}
        for tau in taus:
            key = f"tau_{int(tau * 100)}"
            k = next((i for i, a in enumerate(agreement) if a >= tau), None)
            first[key] = k
            saved[key] = (sum(layer_bytes[k + 1:]) if k is not None else 0)
        return {
            "n_tokens": n_tok,
            "n_layers": self.n_layers,
            "agreement_per_layer": [round(a, 4) for a in agreement],
            "final_entropy_mean": sum(self.final_entropy) / n_tok,
            "first_layer_at": first,
            "bytes_saved_if_exit_at": saved,
            "entropy_per_layer_mean": [
                round(sum(e) / len(e), 4) if e else None
                for e in self._per_layer_entropy
            ],
        }
