"""Tests unitarios del Block Registry — fórmulas canónicas (PRD §2.2/§2.4).

El cfg es un namespace mínimo con valores tipo Qwen3-0.6B (hidden=1024,
heads=16, head_dim=128 → n_heads·head_dim=2048 ≠ hidden: la trampa del
hidden²). Sin red, sin pesos.
"""

from types import SimpleNamespace

from hlwss.registry import BlockRegistry, head_dim


def make_cfg(**over):
    base = dict(hidden_size=1024, vocab_size=151936, tie_word_embeddings=True,
                sliding_window=None, num_hidden_layers=28,
                num_attention_heads=16, num_key_value_heads=8,
                intermediate_size=3072, max_position_embeddings=32768)
    base.update(over)
    return SimpleNamespace(**base)


def test_head_dim_derived():
    assert head_dim(make_cfg()) == 64  # 1024/16
    assert head_dim(make_cfg(head_dim=128)) == 128  # explícito gana


def test_attn_not_hidden_squared():
    reg = BlockRegistry.from_config(make_cfg(), 2)
    # correcto: 2·h·n_heads·hd·d + 2·h·n_kv·hd·d = 2·1024·16·64·2 + 2·1024·8·64·2
    assert reg.size("L0.attn") == 2 * 1024 * 16 * 64 * 2 + 2 * 1024 * 8 * 64 * 2
    # la forma corta hidden² daría un número distinto (33% abajo)
    assert reg.size("L0.attn") != 2 * 1024 * 1024 * 2 + 2 * 1024 * 1024 * 2


def test_embed_is_biggest_and_lm_head_tied():
    reg = BlockRegistry.from_config(make_cfg(), 2)
    assert reg.size("embed") == 151936 * 1024 * 2
    assert reg.blocks()["lm_head"].tied_with == "embed"
    assert reg.size("lm_head") == 0                     # dedup: almacenamiento
    assert reg.touch_size("lm_head") == reg.size("embed")  # touched: resuelve tie


def test_untied_lm_head():
    reg = BlockRegistry.from_config(make_cfg(tie_word_embeddings=False), 2)
    assert reg.blocks()["lm_head"].tied_with is None
    assert reg.size("lm_head") == reg.size("embed")


def test_kv_pages():
    reg = BlockRegistry.from_config(make_cfg(), 2)  # page=16 tokens
    assert reg.kv_page_bytes() == 2 * 8 * 64 * 2 * 16
    assert reg.kv_pages(3, 1) == ["L3.kv.p0"]
    assert reg.kv_pages(3, 16) == ["L3.kv.p0"]
    assert reg.kv_pages(3, 17) == ["L3.kv.p0", "L3.kv.p1"]


def test_kv_aggregate_entry():
    reg = BlockRegistry.from_config(make_cfg(), 2)
    meta = reg.blocks()["L5.kv"]
    assert meta.aggregated and meta.derived
    assert meta.size_bytes == reg.kv_page_bytes() * (32768 // 16)


def test_kv_model_layers_full_and_swa():
    assert BlockRegistry.from_config(make_cfg(), 2).kv_model_layers()[0] == "full"
    swa = BlockRegistry.from_config(make_cfg(sliding_window=4096), 2).kv_model_layers()
    assert swa[27] == "swa:4096"


def test_block_id_for_module_qwen3_conventions():
    reg = BlockRegistry.from_config(make_cfg(), 2)
    assert reg.block_id_for_module("model.embed_tokens") == "embed"
    assert reg.block_id_for_module("model.layers.5.self_attn") == "L5.attn"
    assert reg.block_id_for_module("model.layers.5.mlp") == "L5.ffn"
    assert reg.block_id_for_module("lm_head") == "lm_head"
    assert reg.block_id_for_module("model.norm") is None


def test_dense_mlp_children_own_no_block():
    # regresión: en un denso, SOLO el container mlp posee L{i}.ffn. Las
    # proyecciones (gate/up/down, act_fn) no — si mapearan a ffn, el bloque
    # se emitiría 5× por paso (bug real en Qwen3-0.6B, V1 77% off).
    reg = BlockRegistry.from_config(make_cfg(), 2)
    for child in ("gate_proj", "up_proj", "down_proj", "act_fn"):
        assert reg.block_id_for_module(f"model.layers.0.mlp.{child}") is None
    assert reg.block_id_for_module("model.layers.0.mlp") == "L0.ffn"
    assert reg.block_id_for_module("model.layers.0.self_attn.q_proj") is None


def test_moe_blocks():
    cfg = make_cfg(num_experts=8, num_experts_per_tok=2, moe_intermediate_size=256)
    reg = BlockRegistry.from_config(cfg, 2)
    assert reg.size("L0.expert.7") == 3 * 1024 * 256 * 2
    assert reg.block_id_for_module("model.layers.2.mlp.gate") == "router"
    assert reg.block_id_for_module("model.layers.2.mlp.experts.3") == "L2.expert.3"


def test_sidecar_shape():
    sc = BlockRegistry.from_config(make_cfg(), 2).sidecar()
    assert sc["n_layers"] == 28 and sc["n_experts"] == 0
    assert sc["context_window"] == 32768 and sc["dtype_bytes"] == 2
    assert sc["kv_pages_template"]["kv_page_tokens"] == 16
    assert sc["kv_model_layers"]["0"] == "full"


def _moe_cfg(**over):
    base = dict(hidden_size=128, vocab_size=1000, num_hidden_layers=2,
                num_attention_heads=4, num_key_value_heads=2, num_experts=4,
                num_experts_per_tok=2, moe_intermediate_size=64,
                intermediate_size=64, tie_word_embeddings=False,
                sliding_window=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_shared_expert_solo_container_posee_bloque():
    # regresión Qwen1.5-MoE: los hijos del shared_expert (gate/up/down/act)
    # mapeaban al mismo bid → 5 emissions por paso (bytes 5× inflados)
    reg = BlockRegistry(_moe_cfg(), 2, 16)
    f = reg.block_id_for_module
    assert f("model.layers.3.mlp.shared_expert") == "L3.shared_expert"
    assert f("model.layers.3.mlp.shared_expert.gate_proj") is None
    assert f("model.layers.3.mlp.shared_expert.act_fn") is None


def test_experto_individual_solo_container_posee_bloque():
    # modelos MoE no fusionados: el experto N posee el bloque, sus
    # proyecciones internas no (misma clase de bug, latente hasta hoy)
    reg = BlockRegistry(_moe_cfg(), 2, 16)
    f = reg.block_id_for_module
    assert f("model.layers.2.mlp.experts.5") == "L2.expert.5"
    assert f("model.layers.2.mlp.experts.5.gate_proj") is None


# ---- Gemma 3n (ADR-0067) — fixtures sintéticos, sin modelo ------------------

def make_gemma3n_cfg(**over):
    """Wrapper Gemma3nConfig mínimo: valores tipo E2B (21 capas, 6
    compartidas KV) pero chicos para contar a mano."""
    text = SimpleNamespace(
        model_type="gemma3n_text", hidden_size=256, vocab_size=1000,
        tie_word_embeddings=True, num_hidden_layers=3,
        num_attention_heads=2, num_key_value_heads=1, head_dim=128,
        intermediate_size=[512, 768, 512],  # MatFormer: lista por capa
        max_position_embeddings=4096, sliding_window=512,
        layer_types=["full_attention", "sliding_attention", "sliding_attention"],
        vocab_size_per_layer_input=900, hidden_size_per_layer_input=64,
        num_kv_shared_layers=1, laurel_rank=32, altup_num_inputs=4,
        num_experts=0)
    base = dict(model_type="gemma3n", text_config=text)
    base.update(over)
    return SimpleNamespace(**base)


def test_gemma3n_build_familias():
    reg = BlockRegistry.from_config(make_gemma3n_cfg(), 2)
    b = reg.blocks()
    assert "L0.ple" in b and b["L0.ple"].component == "PLE"
    assert "L0.aug" in b and b["L0.aug"].component == "AUG"
    assert "ple.proj" in b
    assert not any(".expert." in k for k in b)


def test_gemma3n_ffn_matformer_por_capa():
    reg = BlockRegistry.from_config(make_gemma3n_cfg(), 2)
    assert reg.size("L0.ffn") == 3 * 256 * 512 * 2
    assert reg.size("L1.ffn") == 3 * 256 * 768 * 2  # intermedio mayor capa 1
    assert reg.size("L2.ffn") == 3 * 256 * 512 * 2


def test_gemma3n_capa_compartida_sin_kv():
    # 3 capas, num_kv_shared_layers=1 → L2 (la última) solo tiene q+o
    reg = BlockRegistry.from_config(make_gemma3n_cfg(), 2)
    h, hd, d = 256, 128, 2
    qo = 2 * h * 2 * hd * d
    kv = 2 * h * 1 * hd * d
    assert reg.size("L0.attn") == qo + kv
    assert reg.size("L2.attn") == qo          # sin k_proj/v_proj
    assert reg.size("L2.attn") < reg.size("L0.attn")


def test_gemma3n_ple_sizes():
    reg = BlockRegistry.from_config(make_gemma3n_cfg(), 2)
    assert reg.size("L0.ple") == 900 * 64 * 2       # tabla por capa
    assert reg.size("ple.proj") == 256 * 3 * 64 * 2  # proyección global
    # aug = laurel(2·h·rank + h) + altup(h·n + n³ + n² + 2h) + gates(2·h·ple_dim), ×d
    # (fórmula validada contra parámetros reales del checkpoint E2B)
    aug = (2 * 256 * 32 + 256 + (256 * 4 + 64 + 16 + 2 * 256) + 2 * 256 * 64) * 2
    assert reg.size("L1.aug") == aug


def test_gemma3n_module_names():
    reg = BlockRegistry.from_config(make_gemma3n_cfg(), 2)
    f = reg.block_id_for_module
    assert f("model.language_model.embed_tokens") == "embed"
    assert f("model.language_model.embed_tokens_per_layer") == "ple"
    assert f("model.language_model.per_layer_model_projection") == "ple.proj"
    assert f("model.language_model.layers.1.self_attn") == "L1.attn"
    assert f("model.language_model.layers.1.mlp") == "L1.ffn"
    assert f("model.language_model.layers.1.laurel") == "L1.aug"  # owner único
    # altup se llama vía .predict()/.correct() (sin hook); gates/proyección
    # por capa: sus pesos ya viven en el bloque de laurel (regla mlp→ffn)
    assert f("model.language_model.layers.1.altup") is None
    assert f("model.language_model.layers.1.per_layer_input_gate") is None
    assert f("model.language_model.layers.1.per_layer_projection") is None
    assert f("model.language_model.layers.1.input_layernorm") is None
    assert f("lm_head") == "lm_head"
    assert f("model.vision_tower.blocks.0") is None   # towers fuera en G1
    assert f("model.audio_tower") is None
    # Gemma3nForCausalLM (text-only) omite el segmento language_model.
    assert f("model.embed_tokens") == "embed"
    assert f("model.embed_tokens_per_layer") == "ple"
    assert f("model.per_layer_model_projection") == "ple.proj"
    assert f("model.layers.1.self_attn") == "L1.attn"
    assert f("model.layers.1.laurel") == "L1.aug"
    assert f("model.layers.1.per_layer_projection") is None


def test_gemma3n_kv_model_layers_hibrido():
    reg = BlockRegistry.from_config(make_gemma3n_cfg(), 2)
    kinds = reg.kv_model_layers()
    assert kinds[0] == "full"
    assert kinds[1] == "swa:512"
    assert kinds[2] == "swa:512"
