"""Tests de EarlyExitProbe (E1-A, ADR-0062) — MoE sintético tiny en CPU."""

import json

import torch
from transformers import Qwen3MoeConfig, Qwen3MoeForCausalLM

from hlwss.early_exit import EarlyExitProbe


def tiny_moe(seed=0) -> Qwen3MoeForCausalLM:
    torch.manual_seed(seed)
    cfg = Qwen3MoeConfig(
        vocab_size=128, hidden_size=64, intermediate_size=128,
        num_hidden_layers=3, num_attention_heads=4, num_key_value_heads=2,
        num_experts=4, num_experts_per_tok=2, moe_intermediate_size=64,
        max_position_embeddings=64, tie_word_embeddings=False,
    )
    return Qwen3MoeForCausalLM(cfg).eval()


def test_stats_entropia_conocida():
    # entropía de una distribución conocida: no debe colapsar a 0 por el clamp
    probe = EarlyExitProbe(tiny_moe())
    logits = torch.zeros(4)
    logits[0] = 2.0  # softmax = [0.711, 0.096, 0.096, 0.096] → H ≈ 0.918 nats
    am, ent, t1 = probe._stats(logits)
    assert am == 0
    assert 0.9 < ent < 0.95
    assert 0.70 < t1 < 0.72


def test_ultima_capa_acuerda_perfectamente():
    model = tiny_moe()
    probe = EarlyExitProbe(model)
    probe.attach()
    gen = probe_loop(model, [1, 2, 3, 4], max_tokens=6, probe=probe)
    probe.detach()
    rep = probe.report()
    assert rep["n_tokens"] == len(gen)
    # la última capa proyectada = el camino exacto del forward final
    assert rep["agreement_per_layer"][-1] == 1.0
    assert all(0.0 <= a <= 1.0 for a in rep["agreement_per_layer"])
    assert len(rep["agreement_per_layer"]) == 3


def test_alineacion_token_por_token():
    # cada capa vio exactamente un argmax por token generado (prefill incluido)
    model = tiny_moe()
    probe = EarlyExitProbe(model)
    probe.attach()
    gen = probe_loop(model, [5, 6, 7], max_tokens=5, probe=probe)
    probe.detach()
    for i in range(probe.n_layers):
        assert len(probe._per_layer_argmax[i]) == len(gen)


def test_bytes_ahorrables_consistentes():
    model = tiny_moe()
    probe = EarlyExitProbe(model)
    probe.attach()
    probe_loop(model, [1, 2], max_tokens=4, probe=probe)
    probe.detach()
    rep = probe.report(taus=(1.0,))
    layer_bytes = probe._layer_bytes()
    total = sum(layer_bytes)
    # salir en la última capa ahorra 0; salir antes ahorra ≤ total − capa 0
    assert all(0 <= v <= total for v in rep["bytes_saved_if_exit_at"].values())
    k = rep["first_layer_at"]["tau_100"]
    if k is not None:
        assert rep["bytes_saved_if_exit_at"]["tau_100"] == sum(layer_bytes[k + 1:])


def test_compone_con_adapter_sin_doble_lm_head():
    # el hook del lm_head no debe registrar las proyecciones propias del probe
    from hlwss.adapter_torch import Adapter
    from hlwss.observer import Observer, RunConfig
    from hlwss.registry import BlockRegistry

    model = tiny_moe()
    registry = BlockRegistry.from_config(model.config, model.dtype.itemsize, 16)
    observer = Observer()
    run_cfg = RunConfig(model_id="synthetic", prompts_file="t", prompt_hash="t",
                        seed=0, temperature=0.0, do_sample=False,
                        max_tokens=4, device="cpu")
    adapter = Adapter(model, registry, observer)
    probe = EarlyExitProbe(model)
    probe.attach()
    with observer.capture(run_cfg) as trace:
        adapter.instrument()
        gen = adapter.run([1, 2, 3], max_tokens=4)
        adapter.remove()
    probe.detach()
    # 1 registro final por token, no 1 + n_layers (que sería el doble-proyección)
    assert len(probe.final_argmax) == len(gen)
    assert trace.events, "la traza HLWSS sigue capturando eventos con probe activo"


def test_cli_flag_escribe_metadata(tmp_path):
    from hlwss.cli import main

    rc = main(["run", "--synthetic-moe", "--max-tokens", "4",
               "--early-exit-probe", "--out", str(tmp_path)])
    assert rc == 0
    metas = list(tmp_path.glob("*/metadata.json"))
    assert len(metas) == 1
    meta = json.loads(metas[0].read_text())
    assert "early_exit" in meta
    assert meta["early_exit"]["agreement_per_layer"][-1] == 1.0


# ---- helper: loop greedy mínimo (el probe no lleva loop propio por diseño;
# en producción conduce el Adapter — acá se replica para aislar el probe) ----

@torch.no_grad()
def probe_loop(model, prompt_ids, max_tokens, probe):
    ids = torch.tensor([prompt_ids])
    out = model(input_ids=ids, use_cache=True)
    past = out.past_key_values
    nxt = int(out.logits[0, -1].argmax())
    gen = [nxt]
    for _ in range(1, max_tokens):
        out = model(input_ids=torch.tensor([[nxt]]), past_key_values=past,
                    use_cache=True)
        past = out.past_key_values
        nxt = int(out.logits[0, -1].argmax())
        gen.append(nxt)
    return gen


def test_report_early_exit_agrega_curvas(tmp_path):
    import json
    from types import SimpleNamespace

    from hlwss.cli import cmd_report_early_exit

    def fake_meta(prompt_idx, curve):
        d = tmp_path / f"run-{prompt_idx}"
        d.mkdir()
        (d / "metadata.json").write_text(json.dumps({
            "model_id": "m", "prompt_index": prompt_idx,
            "early_exit": {"n_tokens": 10, "agreement_per_layer": curve},
        }))
    fake_meta(0, [0.0, 0.5, 1.0])
    fake_meta(1, [0.0, 0.0, 1.0])
    (tmp_path / "run-sin-probe").mkdir()
    (tmp_path / "run-sin-probe" / "metadata.json").write_text(
        json.dumps({"model_id": "m"}))  # sin early_exit: se ignora
    a = SimpleNamespace(traces=str(tmp_path), model="m", csv=None)
    assert cmd_report_early_exit(a) == 0
    # run-1 nunca llega a 0.9 antes de la última capa → 1/2 con ahorro>0
