"""Tests del Simulator Fase 0.7 (ADR-0065) y Fase 1 preflight (ADR-0066) —
fixtures sintéticos, sin red."""

import json

import pandas as pd

from hlwss.simulate import (_block_size, _pinned, _run_lru, _run_oracle,
                            _run_slru, load_run, scale_blocks, simulate)

BLOCKS = {"embed": 100, "L0.attn": 100, "L0.shared_expert": 100,
          "L0.expert.0": 100, "L0.expert.1": 100, "L0.expert.2": 100}
STREAM = ["L0.expert.0", "L0.expert.1", "L0.expert.2", "L0.expert.0"]


def test_lru_bytes_exactos():
    # budget 200, tamaño 100: C expulsa A (LRU), el segundo toque de A es miss
    hits, transferred, evictions, _ = _run_lru(
        [("A", 100), ("B", 100), ("C", 100), ("A", 100)], 200)
    assert (hits, transferred, evictions) == (0, 400, 2)


def test_oracle_menor_igual_que_lru():
    import random
    random.seed(7)
    seq = [f"B{random.randint(0, 7)}" for _ in range(400)]
    touches = [(b, 100) for b in seq]
    _, t_lru, _, _ = _run_lru(touches, 300)
    _, t_orc, _, _ = _run_oracle(touches, 300)
    assert t_orc <= t_lru


def test_pinning_nunca_evicta():
    # pinned={A} no-pinneado: budget 200 → cold_cap=40 < tamaño 100, así que
    # cada insert se auto-evicta; los toques de A siguen siendo hit directo
    pinned = {"A"}
    touches = [("A", 100), ("E0", 100), ("E1", 100), ("A", 100), ("E0", 100)]
    hits, transferred, evictions, _ = _run_slru(touches, 200, pinned)
    assert hits == 1           # 1er toque de A = carga en frío (miss), 2º = hit
    assert transferred == 300  # E0 dos veces + E1 una
    assert evictions == 3


def test_slru_promocion_sobrevive_donde_lru_evicta():
    # budget 1000 → hot 800 / cold 200 (caben 2 bloques). X se re-toca en la
    # 3ra posición y asciende a hot (último toque). Después entran 10 bloques
    # nuevos (> capacidad): el LRU puro expulsa a X (LRU entre los residentes
    # en la 2da evicción) justo antes del toque final; el SLRU lo mantiene en
    # hot porque ninguna presión toca el segmento hot.
    pinned: set = set()
    touches = [(b, 100) for b in
               ("X", "Y", "X", "Z", "W", "V", "U", "T", "S", "R", "Q", "K", "J", "X")]
    hits_s, tr_s, *_ = _run_slru(touches, 1000, pinned)
    hits_l, tr_l, *_ = _run_lru(touches, 1000)
    assert hits_s == 2 and hits_l == 1
    assert tr_s < tr_l


def test_infeasible_pinned_mayor_que_budget():
    blocks = {"embed": 1000, "L0.expert.0": 100}
    stream = ["embed", "L0.expert.0"]
    r = simulate(stream, blocks, None, "hlwss", 500)
    assert r["infeasible"] and r["pinned_bytes"] == 1100  # embed + E0 (único
    # experto: cubre el 90% de los accesos EXPERT por sí solo)


def test_block_id_desconocido_se_salta():
    r = simulate(["ZZZ", "embed"], BLOCKS, None, "lru", 10**9)
    assert r["n_unknown"] == 1 and r["touches"] == 1


def test_kv_page_usa_template_no_evento():
    assert _block_size(BLOCKS, 4096, "L3.kv.p0") == 4096
    assert _block_size(BLOCKS, 4096, "embed") == 100
    r = simulate(["L3.kv.p0", "L3.kv.p0"], BLOCKS, 4096, "lru", 10**9)
    assert r["bytes_transferred"] == 4096  # segundo toque = hit
    assert r["n_unknown"] == 0


def test_pinned_cubre_90pct_expertos():
    # 13 accesos EXPERT: E0×9, E2×3, E1×1. Umbral 90% = 11.7: E0 (9) no basta,
    # E2 (→12) cruza; E1, el menos frecuente, queda fuera. shared_expert solo
    # entra al pin si el modelo realmente lo tocó (semántica touched-only).
    stream = (["L0.expert.0"] * 9 + ["L0.expert.1"] + ["L0.expert.2"] * 3 +
              ["embed", "L0.shared_expert"])
    pin = _pinned(BLOCKS, stream)
    assert "L0.expert.0" in pin and "L0.expert.2" in pin
    assert "L0.expert.1" not in pin
    assert "embed" in pin and "L0.shared_expert" in pin


def test_pin_paga_su_carga_en_frio():
    # todo pinneado → transferred == pin_bytes exactamente (carga única al
    # t=0); si el pin fuera gratis este número saldría 0 (artefacto contable)
    blocks = {"embed": 100, "L0.expert.0": 100, "L0.expert.1": 100}
    stream = ["embed", "L0.expert.0", "L0.expert.1"] * 2
    r = simulate(stream, blocks, None, "hlwss", 10**6)
    assert r["bytes_transferred"] == 300
    assert r["ram_hit_fraction"] == 0.5  # 3 hits de 6 toques (2da pasada)


def test_load_run_ordena_por_timestamp(tmp_path):
    d = tmp_path / "run1"
    d.mkdir()
    df = pd.DataFrame({
        "timestamp_ns": [3, 1, 2],
        "block_id": pd.array(["B", "A", "B"], dtype="string"),
        "event_type": ["LOGICAL_ACCESS", "LOGICAL_ACCESS", "LOGICAL_ACCESS"],
    })
    df.to_parquet(d / "events.parquet")
    (d / "metadata.json").write_text(json.dumps({
        "registry": {"blocks": {"A": {"size_bytes": 100},
                                "lm_head": {"size_bytes": 0}},
                     "kv_pages_template": {"kv_page_bytes": 4096}}}))
    stream, blocks, kv, meta = load_run(tmp_path, "run1")
    assert stream == ["A", "B", "B"]      # orden temporal, no físico
    assert blocks == {"A": 100}           # tie (lm_head 0) excluido
    assert kv == 4096 and meta["registry"]


# ---- Fase 1 preflight (ADR-0066): política hlwss-adaptive -------------------

def test_adaptiva_fit_degrada_a_lru():
    # WS único (300) <= budget → régimen fit: pin vacío, sin cold-load, y
    # bytes idénticos a lru (el pin+SLRU sería overhead puro)
    blocks = {"embed": 100, "L0.expert.0": 100, "L0.expert.1": 100}
    stream = ["embed", "L0.expert.0", "L0.expert.1"] * 2
    r_lru = simulate(stream, blocks, None, "lru", 10**6)
    r = simulate(stream, blocks, None, "hlwss-adaptive", 10**6)
    assert r["regime"] == "fit" and r["pinned_bytes"] == 0
    assert r["bytes_transferred"] == r_lru["bytes_transferred"] == 300


def test_adaptiva_shrink_to_fit():
    # budget 380: pin_cap=342; base=100 → cupo experto 242. E0(200) entra,
    # E2(200) no (400>242): cobertura efectiva 9/13=0.69 < target 0.9, y la
    # política sigue siendo feasible (antes: infeasible bajo 7.38GB real)
    blocks = {"embed": 100, "L0.expert.0": 200,
              "L0.expert.1": 200, "L0.expert.2": 200}
    stream = (["embed"] + ["L0.expert.0"] * 9 + ["L0.expert.1"]
              + ["L0.expert.2"] * 3)
    r = simulate(stream, blocks, None, "hlwss-adaptive", 380)
    assert r["regime"] == "spill" and not r["infeasible"]
    assert r["coverage"] < 0.9
    assert r["pinned_bytes"] == 300 <= 0.9 * 380
    assert "L0.expert.2" not in _pinned(blocks, stream, pin_cap=342)


def test_adaptiva_base_no_expert_mayor_que_budget():
    # base no-expert (1000) > pin_cap (0.9*600=540): pin = base sola, sin
    # expertos; pin_bytes > budget → infeasible marcado, sin crash
    blocks = {"embed": 1000, "L0.expert.0": 100}
    stream = ["embed", "L0.expert.0"]
    r = simulate(stream, blocks, None, "hlwss-adaptive", 600)
    assert r["regime"] == "spill" and r["infeasible"]
    assert r["pinned_bytes"] == 1000


def test_adaptiva_cold_load_contable_en_spill():
    # spill: pin {embed, E0}=200 paga su carga; SLRU budget=50 no retiene
    # E1 (100>cold_cap 40) → E1 se transfiere en cada toque (2×100)
    blocks = {"embed": 100, "L0.expert.0": 100, "L0.expert.1": 100}
    stream = ["embed", "L0.expert.0", "L0.expert.1"] * 2
    r = simulate(stream, blocks, None, "hlwss-adaptive", 250)
    assert r["regime"] == "spill" and r["pinned_bytes"] == 200
    assert r["bytes_transferred"] == 400  # 200 pin + 2×100 de E1
    assert r["ram_hit_fraction"] == 2 / 6  # embed+E0 en la 2a pasada


def test_adaptiva_paridad_con_estatica_en_spill():
    # spill con cupo NO limitante: mismo pin que hlwss estático → mismos
    # bytes; la adaptatividad no debe cambiar nada donde la estática cabe
    blocks = {"embed": 100, "L0.expert.0": 200,
              "L0.expert.1": 200, "L0.expert.2": 200}
    stream = (["embed"] + ["L0.expert.0"] * 9 + ["L0.expert.1"]
              + ["L0.expert.2"] * 3)
    r_static = simulate(stream, blocks, None, "hlwss", 699)
    r = simulate(stream, blocks, None, "hlwss-adaptive", 699)
    assert r["regime"] == "spill"
    assert r["pinned_bytes"] == r_static["pinned_bytes"] == 500
    assert r["bytes_transferred"] == r_static["bytes_transferred"]
    assert abs(r["coverage"] - 12 / 13) < 1e-9  # 12 de 13 toques cubiertos


# ---- scale_blocks (ADR-0068, G2) --------------------------------------------

_META_BLOCKS = {
    "embed": {"component": "EMBED", "size_bytes": 1000},
    "L0.ffn": {"component": "FFN", "size_bytes": 800},
    "L0.ple": {"component": "PLE", "size_bytes": 400},
    "L0.kv": {"component": "KV", "size_bytes": 200, "derived": True},
    "lm_head": {"component": "LM_HEAD", "size_bytes": 0},
}


def test_scale_blocks_sin_factores_es_identidad():
    out = scale_blocks(_META_BLOCKS, {})
    assert out == {"embed": 1000, "L0.ffn": 800, "L0.ple": 400, "L0.kv": 200}


def test_scale_blocks_por_componente():
    out = scale_blocks(_META_BLOCKS, {"PLE": 0.25})
    assert out["L0.ple"] == 100
    assert out["L0.ffn"] == 800          # sin factor: intacto
    assert out["L0.kv"] == 200           # derived: intacto


def test_scale_blocks_weights_y_override():
    # weights aplica a no derivados; el factor explícito del componente gana
    out = scale_blocks(_META_BLOCKS, {"weights": 0.25, "PLE": 0.5})
    assert out["embed"] == 250
    assert out["L0.ffn"] == 200
    assert out["L0.ple"] == 200          # PLE:0.5 gana sobre weights
    assert out["L0.kv"] == 200           # derived: fuera de weights
