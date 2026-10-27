"""Tests de cuantización int4 propia para expertos fusionados (ADR-0063)."""

import torch

from hlwss.quant_int4 import (BYTES_POR_PESO, GROUP, Int4Experts, dequant_int4,
                              pack_int4)
from hlwss.registry import BlockRegistry


def test_pack_dequant_roundtrip():
    torch.manual_seed(0)
    w = torch.randn(60, 256, 128)  # E, O, I múltiplo de GROUP
    packed, scales = pack_int4(w)
    assert packed.shape == (60, 256, 64)          # 2 pesos por byte
    assert scales.shape == (60, 256, 128 // GROUP)
    w_hat = dequant_int4(packed, scales)
    # error L1 típico de absmax-int4 group=64 sobre gaussiana ≈ 11%
    # (paso = absmax/7 ≈ 0.34σ → E|err| ≈ 0.10σ; L1 rel ≈ 0.11). int8 daría ~4× menos.
    rel = (w_hat - w).abs().mean() / w.abs().mean()
    assert rel < 0.15, f"error relativo {rel:.3f} fuera de rango int4"


def test_bytes_por_peso_documentado():
    assert 0.52 < BYTES_POR_PESO < 0.54  # 0.5 nibble + escala fp16/64


def _ref_experts_forward(experts, hidden, idx, wts):
    """Referencia: la MISMA lógica contra pesos bf16 (sin cuantizar)."""
    import torch.nn.functional as F
    final = torch.zeros_like(hidden)
    mask = F.one_hot(idx, experts.num_experts).permute(2, 1, 0)
    hit = torch.greater(mask.sum(dim=(-1, -2)), 0).nonzero()
    for e in hit:
        i = int(e[0])
        pos, tok = torch.where(mask[i])
        cur = hidden[tok]
        gate, up = F.linear(cur, experts.gate_up[i]).chunk(2, dim=-1)
        h = F.silu(gate) * up
        h = F.linear(h, experts.down[i])
        h = h * wts[tok, pos, None]
        final.index_add_(0, tok, h.to(final.dtype))
    return final


def test_int4_experts_equivale_a_bf16():
    torch.manual_seed(1)
    n_exp, h, moe_inter = 6, 128, 64  # múltiplos de GROUP=64
    gate_up = torch.randn(n_exp, 2 * moe_inter, h) * 0.1
    down = torch.randn(n_exp, h, moe_inter) * 0.1

    exp = Int4Experts(n_exp, 2)
    exp.pack("gate_up_proj", gate_up)
    exp.pack("down_proj", down)

    hidden = torch.randn(5, h)
    idx = torch.tensor([[0, 3], [1, 2], [0, 5], [3, 4], [2, 1]])  # top-2
    wts = torch.rand(5, 2)

    class _Ref:
        num_experts = n_exp
    ref = _Ref()
    ref.gate_up = gate_up
    ref.down = down
    got = exp(hidden, idx, wts)
    want = _ref_experts_forward(ref, hidden, idx, wts)
    # 1) contra bf16 puro: cota holgada (dos matmuls int4 encadenados + silu
    #    componen el ruido de cuantización ~2×)
    rel = (got - want).abs().mean() / want.abs().clamp_min(1e-6).mean()
    assert rel < 0.30, f"salida diverge demasiado: {rel:.3f}"
    # 2) contra pesos DEQUANTIZADOS: aísla la lógica del ruido — deben
    #    coincidir salvo redondeo bf16 (si falla, hay bug de índices/pares)
    ref_q = _Ref()
    ref_q.num_experts = n_exp
    ref_q.gate_up = torch.stack(
        [exp._dequant("gate_up_proj", i) for i in range(n_exp)])
    ref_q.down = torch.stack(
        [exp._dequant("down_proj", i) for i in range(n_exp)])
    want_q = _ref_experts_forward(ref_q, hidden.to(torch.bfloat16), idx, wts)
    rel_q = (got - want_q).abs().mean() / want_q.abs().clamp_min(1e-6).mean()
    assert rel_q < 0.02, f"lógica diverge de dequant: {rel_q:.3f}"
    assert got.shape == hidden.shape


def test_registry_expert_dtype_bytes_mixto():
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        hidden_size=128, vocab_size=1000, num_hidden_layers=2,
        num_attention_heads=4, num_key_value_heads=2, num_experts=3,
        num_experts_per_tok=1, moe_intermediate_size=64, intermediate_size=64,
        shared_expert_intermediate_size=0, tie_word_embeddings=False,
        sliding_window=None, max_position_embeddings=512,
    )
    reg = BlockRegistry(cfg, 2.0, 16, expert_dtype_bytes=0.53)
    blocks = reg.blocks()
    e0 = blocks["L0.expert.0"].size_bytes
    assert e0 == 3 * 128 * 64 * 0.53
    # shared_expert (si existiera) y attn usan el dtype base
    assert blocks["L0.attn"].size_bytes != 0
    # default sin override: todo con dtype_bytes base (comportamiento previo)
    reg2 = BlockRegistry(cfg, 2.0, 16)
    assert reg2.blocks()["L0.expert.0"].size_bytes == 3 * 128 * 64 * 2.0
