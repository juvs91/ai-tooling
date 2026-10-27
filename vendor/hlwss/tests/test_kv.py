"""Tests de kv_bytes_per_step (PRD §2.4) — función pura, sin modelo."""

from types import SimpleNamespace

from hlwss.adapter_torch import kv_bytes_per_step


def make_cfg(**over):
    base = dict(hidden_size=1024, num_attention_heads=16, num_key_value_heads=8,
                sliding_window=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_full_attention():
    cfg = make_cfg()  # head_dim = 64
    assert kv_bytes_per_step(cfg, 2, 0, 100) == 2 * 8 * 64 * 2 * 100


def test_sliding_window_caps_context():
    cfg = make_cfg(sliding_window=4096)
    assert kv_bytes_per_step(cfg, 2, 0, 100) == 2 * 8 * 64 * 2 * 100      # bajo W
    assert kv_bytes_per_step(cfg, 2, 0, 64000) == 2 * 8 * 64 * 2 * 4096   # min(C, W)


def test_layer_param_reservado():
    # hoy uniforme por capa; el parámetro existe para SWA híbrida (Fase 1)
    cfg = make_cfg()
    assert kv_bytes_per_step(cfg, 2, 3, 100) == kv_bytes_per_step(cfg, 2, 0, 100)
