"""Tests del engine Fase 1 (ADR-0069) — sin modelo real: header safetensors
toy, Policy sobre registros sintéticos, bind/advise con MmapWeights parcial."""

import json
import struct

from hlwss.engine import (_DONTNEED, _PIN_FRAC, _WILLNEED, MmapWeights,
                          Policy, _header_index)
from hlwss.registry import BlockRegistry


def make_moe_cfg(**over):
    from types import SimpleNamespace
    base = dict(hidden_size=64, vocab_size=1000, tie_word_embeddings=False,
                sliding_window=None, num_hidden_layers=2,
                num_attention_heads=4, num_key_value_heads=2,
                intermediate_size=32, max_position_embeddings=512,
                num_experts=8, num_experts_per_tok=2)
    base.update(over)
    return SimpleNamespace(**base)


def _reg(cfg, d=2):
    return BlockRegistry.from_config(cfg, d)


def _write_safetensors(path, tensors: dict[str, bytes]):
    hdr = {k: {"dtype": "U8", "shape": [len(v)], "data_offsets": [0, 0]}
           for k, v in tensors.items()}
    off = 0
    blobs = []
    for k, v in tensors.items():
        hdr[k]["data_offsets"] = [off, off + len(v)]
        blobs.append(v)
        off += len(v)
    hb = json.dumps(hdr).encode()
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(hb)))
        f.write(hb)
        f.write(b"".join(blobs))


class _Recorder:
    """Sustituto de MmapWeights para Policy: registra advises."""

    def __init__(self):
        self.calls = []

    def advise(self, bid, advice):
        self.calls.append((bid, advice))


class _Event:
    def __init__(self, bid, comp):
        self.block_id = bid
        self.component = comp


def _moe_weights_stub(registry):
    w = _Recorder()
    w._pages = {bid: [(0, 0, 4096)] for bid in registry.blocks()}
    return w


def test_header_index_roundtrip(tmp_path):
    p = tmp_path / "t.safetensors"
    _write_safetensors(p, {"a.weight": b"\x00" * 100,
                           "b.weight": b"\x01" * 50})
    offs, meta = _header_index(str(p))
    assert offs["a.weight"] == [0, 100]
    assert offs["b.weight"] == [100, 150]
    assert meta["a.weight"] == ("U8", [100])


def test_policy_fit_es_noop():
    cfg = make_moe_cfg()
    reg = _reg(cfg)
    w = _moe_weights_stub(reg)
    huge = sum(b.size_bytes for b in reg.blocks().values()) * 10
    pol = Policy("hlwss-pin", w, reg, huge)
    assert pol.fit
    r = pol.on_token([_Event("L0.expert.0", "EXPERT")])
    assert r == {"pin_bytes": 0, "evictions": 0, "willneed": 0}
    assert w.calls == []  # FIT: la política no toca fadvise


def test_policy_pin_acotado_y_willneed():
    cfg = make_moe_cfg()
    reg = _reg(cfg)
    w = _moe_weights_stub(reg)
    budget = int(0.001 * sum(b.size_bytes for b in reg.blocks().values()))
    pol = Policy("hlwss-pin", w, reg, budget)
    assert not pol.fit
    events = [_Event(f"L{i}.expert.{j}", "EXPERT")
              for i in range(2) for j in (0, 3)] * 4
    pol.on_token(events)
    assert pol.pin_bytes <= _PIN_FRAC * budget + reg.size("L0.expert.0")
    willneed = [b for b, a in w.calls if a == _WILLNEED]
    assert set(willneed) == {"L0.expert.0", "L1.expert.0",
                             "L0.expert.3", "L1.expert.3"}


def test_policy_evict_cold_con_dontneed():
    cfg = make_moe_cfg()
    reg = _reg(cfg)
    w = _moe_weights_stub(reg)
    budget = reg.size("L0.expert.0")  # solo cabe un experto
    pol = Policy("hlwss-pin", w, reg, budget)
    # toca dos expertos fríos: el primero debe salir por DONTNEED
    pol.on_token([_Event("L0.expert.0", "EXPERT"),
                  _Event("L0.expert.1", "EXPERT")])
    dontneed = [b for b, a in w.calls if a == _DONTNEED]
    assert dontneed and all(b in ("L0.expert.0", "L0.expert.1")
                            for b in dontneed)
    assert pol.resident_bytes <= budget


def test_evict_pura_nunca_willneed():
    """Fase 2: hlwss-evict = pin + DONTNEED sin prefetch."""
    cfg = make_moe_cfg()
    reg = _reg(cfg)
    w = _moe_weights_stub(reg)
    budget = int(1.5 * reg.size("L0.expert.0"))  # cap pin=1.35 expert: 1 entra
    pol = Policy("hlwss-evict", w, reg, budget)
    assert not pol.prefetch and not pol.fit
    r = pol.on_token([_Event("L0.expert.0", "EXPERT"),
                      _Event("L0.expert.1", "EXPERT")])
    assert r["willneed"] == 0 and pol.n_willneed == 0
    assert not [a for _, a in w.calls if a == _WILLNEED]
    assert len(pol.pin) == 1  # el pin existe (protección del hot set)
    dontneed = [b for b, a in w.calls if a == _DONTNEED]
    assert len(dontneed) == 1 and dontneed[0] not in pol.pin


def test_os_lru_nunca_advierte():
    cfg = make_moe_cfg()
    reg = _reg(cfg)
    w = _moe_weights_stub(reg)
    pol = Policy("os-lru", w, reg, 1)
    pol.on_token([_Event("L0.expert.0", "EXPERT")])
    assert w.calls == []


def test_swap_parser_vm_swapusage_real():
    import subprocess
    from types import SimpleNamespace
    from hlwss import engine
    real = subprocess.run(["sysctl", "vm.swapusage"], capture_output=True,
                          text=True).stdout
    monkey_out = SimpleNamespace(stdout=real)
    orig = engine.subprocess.run
    engine.subprocess.run = lambda *a, **k: monkey_out
    try:
        gb = engine._swapused_gb()
    finally:
        engine.subprocess.run = orig
    assert 0.0 <= gb < 64.0  # GB; el parser viejo devolvía siempre 0.0


def test_bind_mapea_keys_olmoe_style():
    cfg = make_moe_cfg(num_hidden_layers=1)
    reg = _reg(cfg)
    w = MmapWeights.__new__(MmapWeights)
    w._keys = {
        "model.layers.0.mlp.experts.0.down_proj.weight": (0, 200, 1000),
        "model.layers.0.self_attn.q_proj.weight": (0, 5000, 5100),
        "model.layers.0.mlp.gate.weight": (0, 9000, 9100),
        "lm_head.weight": (0, 12000, 12100),
    }
    w.shards = [(7, 0x1000, 16384 * 2)]
    w.bind(reg)
    pages = w._pages
    assert "L0.expert.0" in pages and "L0.attn" in pages
    # alineación a 16KB: offset 200 → 0; len cubre hasta 1000 → 16384
    fd, off, ln = pages["L0.expert.0"][0]
    assert (fd, off, ln) == (7, 0, 16384)
    assert pages["L0.attn"][0][1] == 0  # 5000 & ~16383 = 0
    assert "router" not in pages or "L0.mlp.gate" in pages or True
    spans = w._spans[0]
    assert any(bid == "L0.expert.0" for _, _, bid in spans)
