"""Engine Fase 1 (ADR-0069): inferencia CPU sobre pesos file-backed (mmap
zero-copy), budget vía buffer de presión, pageins reales (Δru_majflt × page).
Política = madvise; residencia vía mincore. Alcance: pesos (E7 en ADR-0069)."""

import ctypes
import json
import os
import re
import resource
import struct
import subprocess
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import numpy as np
import torch

from .observer import Observer, RunConfig
from .adapter_torch import Adapter
from .registry import BlockRegistry

PAGE = 16384          # page size Apple Silicon (verificado vm_stat)
_WILLNEED, _DONTNEED = 3, 4     # macOS madvise(2)
_PIN_FRAC = 0.9       # misma constante que simulate.py (_PIN_BUDGET_FRAC)


def _shard_paths(model_id: str) -> list[str]:
    cfg_dir = Path(model_id)
    if not cfg_dir.is_dir():  # cache HF: resolver vía config guardado
        from transformers.utils.hub import cached_file
        cfg_dir = Path(cached_file(model_id, "config.json")).parent
    shards = sorted(cfg_dir.glob("model-*.safetensors")) or \
        list(cfg_dir.glob("*.safetensors"))
    return [str(p) for p in shards]
def _header_index(path: str) -> tuple[dict, dict]:
    """(offsets, meta): key→[b0,b1] y key→(dtype, shape) del header."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        hdr = json.loads(f.read(n))
    offs = {k: v["data_offsets"] for k, v in hdr.items() if k != "__metadata__"}
    meta = {k: (v["dtype"], v["shape"]) for k, v in hdr.items()
            if k != "__metadata__"}
    return offs, meta


_ST_DT = {"BF16": torch.bfloat16, "F16": torch.float16, "F32": torch.float32,
          "F64": torch.float64, "I64": torch.int64, "I32": torch.int32,
          "I16": torch.int16, "I8": torch.int8, "U8": torch.uint8,
          "BOOL": torch.bool}
_NP_DT = {"BF16": np.uint16, "F16": np.float16, "F32": np.float32,
          "F64": np.float64, "I64": np.int64, "I32": np.int32,
          "I16": np.int16, "I8": np.int8, "U8": np.uint8, "BOOL": np.bool_}


def _ram_total_gb() -> float:
    out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                         capture_output=True, text=True).stdout
    return int(out.strip()) / 1e9


def _swapused_gb() -> float:
    out = subprocess.run(["sysctl", "vm.swapusage"], capture_output=True,
                         text=True).stdout
    m = re.search(r"used = (\d+\.?\d*)M", out)
    return float(m.group(1)) / 1024.0 if m else 0.0


class _UnfusedExperts(torch.nn.Module):
    """Expertos OLMoE/Qwen2MoE como vistas por-experto file-backed (transformers
    5.x los fusiona en Parameter anónimo). Misma matemática: dos lineares."""

    def __init__(self, config, gate: list, up: list, down: list):
        super().__init__()
        from transformers.activations import ACT2FN
        self.num_experts = getattr(config, "num_local_experts", None) \
            or config.num_experts
        self.act_fn = ACT2FN[config.hidden_act]
        self._gate, self._up, self._down = gate, up, down

    def forward(self, hidden_states, top_k_index, top_k_weights):
        final = torch.zeros_like(hidden_states)
        mask = torch.nn.functional.one_hot(top_k_index,
                                           num_classes=self.num_experts)
        mask = mask.permute(2, 1, 0)
        hit = torch.greater(mask.sum(dim=(-1, -2)), 0).nonzero()
        for idx in hit:
            j = int(idx[0])
            if j == self.num_experts:
                continue
            pos, tok = torch.where(mask[j])
            x = hidden_states[tok]
            h = self.act_fn(torch.nn.functional.linear(x, self._gate[j])) \
                * torch.nn.functional.linear(x, self._up[j])
            out = torch.nn.functional.linear(h, self._down[j])
            out = out * top_k_weights[tok, pos, None]
            final.index_add_(0, tok, out.to(final.dtype))
        return final


def _unfuse_olmoe_experts(model, state: dict) -> None:
    """Reemplaza mlp.experts (fused 3D) por _UnfusedExperts con vistas mmap."""
    cfg = model.config
    n_layers, n_exp = cfg.num_hidden_layers, getattr(
        cfg, "num_local_experts", None) or cfg.num_experts
    for i in range(n_layers):
        prefix = f"model.layers.{i}.mlp.experts."
        gate = [state[f"{prefix}{j}.gate_proj.weight"] for j in range(n_exp)]
        up = [state[f"{prefix}{j}.up_proj.weight"] for j in range(n_exp)]
        down = [state[f"{prefix}{j}.down_proj.weight"] for j in range(n_exp)]
        model.model.layers[i].mlp.experts = _UnfusedExperts(
            model.config, gate, up, down)


class MmapWeights:
    """Modelo cargado con vistas mmap + mapeo bloque→rango de archivo."""

    def __init__(self, model_id: str):
        from transformers import AutoConfig, AutoModelForCausalLM
        self.cfg = AutoConfig.from_pretrained(model_id)
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.mmap.restype = ctypes.c_void_p
        self.libc.madvise.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                      ctypes.c_int]
        self.libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                      ctypes.c_char_p]
        self.shards: list[tuple[int, int, int]] = []   # (shard_id, addr, len)
        self._bufs: list = []   # np.memmap: mantienen vivos los mmaps
        self._keys: dict[str, tuple[int, int, int]] = {}  # key→(shard,b0,b1)
        self._state: dict[str, torch.Tensor] = {}
        gemma3n = getattr(self.cfg, "model_type", "") == "gemma3n"
        for si, path in enumerate(_shard_paths(model_id)):
            # np.memmap 'r' = PROT_READ MAP_PRIVATE: sin reserva de swap (WRITE la exige → ENOMEM)
            arr = np.memmap(path, dtype=np.uint8, mode="r")
            self._bufs.append(arr)
            addr, length = int(arr.ctypes.data), int(arr.shape[0])
            self.shards.append((si, addr, length))
            offs, meta = _header_index(path)
            for key, (b0, b1) in offs.items():
                if gemma3n and not key.startswith("model.language_model."):
                    continue  # torres multimodales: solo el texto entra
                # meta/header conservan el nombre original del checkpoint
                sk = key.replace("model.language_model.", "model.", 1) \
                    if gemma3n else key
                self._keys[sk] = (si, b0, b1)
                dt, shape = meta[key]
                flat = torch.from_numpy(arr[b0:b1].view(_NP_DT[dt])) \
                    .view(_ST_DT[dt])
                self._state[sk] = flat.view(*shape) if shape else flat[0]
        arch_cfg = getattr(self.cfg, "text_config", self.cfg)
        cls = AutoModelForCausalLM
        if getattr(self.cfg, "model_type", "") == "gemma3n":
            from transformers import Gemma3nForCausalLM
            cls = Gemma3nForCausalLM
        with torch.device("meta"):
            self.model = cls.from_config(arch_cfg) \
                if hasattr(cls, "from_config") else cls(arch_cfg)
        self.model.load_state_dict(self._state, strict=False, assign=True)
        self._addr_of = {f: a for f, a, _ in self.shards}
        self.model.tie_weights()
        if getattr(arch_cfg, "model_type", "") in ("olmoe", "qwen2_moe"):
            _unfuse_olmoe_experts(self.model, self._state)
        for name, buf in self.model.named_buffers():  # no persistentes en meta
            if not buf.is_meta:
                continue
            mod = self.model.get_submodule(name.rsplit(".", 1)[0])
            base = name.rsplit(".", 1)[-1]
            if base.endswith("inv_freq"):
                lt = base.removesuffix("_inv_freq")
                rp = getattr(arch_cfg, "rope_parameters", {})
                theta = rp.get(lt, {}).get("rope_theta", 10000.0)
                dim = buf.shape[0] * 2
                val = 1.0 / theta ** (torch.arange(0, dim, 2,
                                                   dtype=torch.float32) / dim)
            elif hasattr(mod, "scalar_embed_scale"):  # gemma3n embed_scale
                val = torch.tensor(float(mod.scalar_embed_scale))
            elif base in ("router_input_scale", "per_layer_projection_scale"):
                e = -1.0 if base == "router_input_scale" else -0.5
                val = torch.tensor(float(arch_cfg.hidden_size) ** e)
            elif base == "per_layer_input_scale":  # gemma3n: 1/sqrt(2)
                val = torch.rsqrt(torch.tensor(2.0))
            else:
                raise RuntimeError(f"buffer meta sin fórmula: {name}")
            mod.register_buffer(base, val, persistent=False)
        meta_left = [n for n, p in self.model.named_parameters() if p.is_meta]
        if meta_left:
            raise RuntimeError(f"parámetros en meta sin cargar: {meta_left[:5]}")
        self.model.eval()
        self.dtype_bytes = next(self.model.parameters()).element_size()

    def bind(self, registry: BlockRegistry) -> None:
        """bid→páginas alineadas y spans por shard para atribución."""
        self._pages: dict[str, list[tuple[int, int, int]]] = {}
        self._spans: list[list[tuple[int, int, str]]] = []
        for fd, addr, length in self.shards:
            spans = []
            for key, (si, b0, b1) in self._keys.items():
                if self.shards[si][0] != fd:
                    continue
                # hojas (q_proj, down_proj…) sin bloque: ascender al contenedor propietario (registry.py)
                mod = key.removesuffix(".weight").removesuffix(".bias")
                bid = None
                while mod:
                    bid = registry.block_id_for_module(mod)
                    if bid == "router":        # bloque lógico del adapter V2
                        bid = None
                        break
                    if bid is not None or "." not in mod:
                        break
                    mod = mod.rsplit(".", 1)[0]
                # 'ple'/'ple.proj' (gemma3n): marcadores del adapter, no bloques físicos
                if bid is None or bid not in registry.blocks():
                    continue
                a0, a1 = b0 & ~(PAGE - 1), min(((b1 - 1) | (PAGE - 1)) + 1,
                                               length)
                self._pages.setdefault(bid, []).append((fd, a0, a1 - a0))
                spans.append((a0 // PAGE, (a1 + PAGE - 1) // PAGE, bid))
            spans.sort()
            self._spans.append(spans)

    def _span_addr(self, fd: int, off: int) -> int:
        return self._addr_of[fd] + off

    def advise(self, bid: str, advice: int) -> None:
        for fd, off, ln in self._pages.get(bid, []):
            self.libc.madvise(self._span_addr(fd, off), ln, advice)

    def residency(self, prev: list[np.ndarray] | None):
        """mincore por shard → (vecs, resident_por_bid, no_resident_bytes)."""
        vecs, resident, nonres = [], Counter(), 0
        for idx, (fd, addr, length) in enumerate(self.shards):
            np_ = (length + PAGE - 1) // PAGE
            vec = ctypes.create_string_buffer(np_)
            ok = self.libc.mincore(addr, length, vec) == 0
            arr = np.frombuffer(vec, dtype=np.uint8).copy() if ok \
                else np.zeros(np_, dtype=np.uint8)
            vecs.append(arr)
            nonres += int(np.count_nonzero(arr == 0)) * PAGE
            for s, e, bid in self._spans[idx]:
                resident[bid] += int(np.count_nonzero(arr[s:e] & 1))
        return vecs, resident, nonres


class Policy:
    """Política online. os-lru = no-op; hlwss-pin = pin frecuencia running +
    WILLNEED; hlwss-evict = pin + evicción DONTNEED sin prefetch (Fase 2).
    FIT a priori → no-op (cierra caveat #1 ADR-0066)."""

    def __init__(self, name: str, weights: MmapWeights, registry,
                 budget_bytes: int):
        self.name, self.w = name, weights
        self.reg, self.budget = registry, budget_bytes
        self.prefetch = name == "hlwss-pin"  # evict-pura: solo DONTNEED
        self.known = set(registry.blocks())
        self.ws_total = sum(b.size_bytes for b in registry.blocks().values()
                            if b.size_bytes)
        self.fit = self.ws_total <= budget_bytes
        self.freq: Counter = Counter()
        self.pin: set[str] = set()
        self.pin_bytes = 0
        self.resident: dict[str, int] = {}
        self.resident_bytes = 0
        self.n_evictions = self.n_willneed = 0

    def on_token(self, events) -> dict:
        if self.name == "os-lru" or self.fit:
            return {"pin_bytes": 0, "evictions": 0, "willneed": 0}
        n_adv = 0
        for e in events:
            if e.component == "EXPERT":
                self.freq[e.block_id] += 1
            if e.block_id in self.known:
                if e.block_id not in self.resident:
                    self.resident[e.block_id] = self.reg.size(e.block_id)
                    self.resident_bytes += self.resident[e.block_id]
                if self.prefetch:
                    self.w.advise(e.block_id, _WILLNEED)
                    self.n_willneed += 1
                    n_adv += 1
        self._rebuild_pin()
        return {"pin_bytes": self.pin_bytes,
                "evictions": self._evict_cold(),
                "willneed": n_adv}

    def _rebuild_pin(self) -> None:
        if not self.freq:
            return
        cap, n_exp = int(_PIN_FRAC * self.budget), sum(self.freq.values())
        covered = exp_bytes = 0; pin = set()
        for bid, c in self.freq.most_common():
            sz = self.reg.size(bid)
            if covered >= _PIN_FRAC * n_exp or exp_bytes + sz > cap:
                break
            pin.add(bid)
            exp_bytes, covered = exp_bytes + sz, covered + c
        self.pin, self.pin_bytes = pin, exp_bytes

    def _evict_cold(self) -> int:
        evicted = 0
        while self.resident_bytes > self.budget and self.resident:
            cold = sorted((b for b in self.resident if b not in self.pin),
                          key=lambda b: (self.freq.get(b, 0), b))
            if not cold:
                break
            self.w.advise(cold[0], _DONTNEED)
            self.resident_bytes -= self.resident.pop(cold[0])
            evicted += 1; self.n_evictions += 1
        return evicted
def run_engine(model_id: str, budget_gb: float, policy_name: str,
               max_tokens: int, prompt_text: str, out_dir: str,
               slack_gb: float = 0.0, calibrate: bool = False,
               temperature: float = 0.0) -> dict:
    """Carga mmap + presión, genera greedy (T=0) o sampleada, report + JSONL."""
    from transformers import AutoTokenizer
    t_start = time.time()
    majflt0 = resource.getrusage(resource.RUSAGE_SELF).ru_majflt
    w = MmapWeights(model_id)
    cfg = w.cfg
    registry = BlockRegistry.from_config(cfg, w.dtype_bytes, 16)
    w.bind(registry)
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = w.model
    budget_bytes = int(budget_gb * 1e9)
    ram = _ram_total_gb()
    pressure_gb = max(0.0, ram - budget_gb - slack_gb)
    pressure = torch.empty(int(pressure_gb * 1e9), dtype=torch.uint8)
    pressure.fill_(1)  # sucio: el OS no puede reclamarlo limpio
    observer = Observer()
    run_id = str(uuid.uuid4())
    run_cfg = RunConfig(model_id=model_id, prompts_file="engine",
                        prompt_hash="engine", seed=42, temperature=temperature,
                        do_sample=False, max_tokens=max_tokens, device="cpu")
    adapter = Adapter(model, registry, observer)
    policy = Policy(policy_name, w, registry, budget_bytes)
    rows, pol_rows = [], []
    prev_vec = None

    def drain(phase: str, t) -> None:
        nonlocal prev_vec
        events = observer.trace.events[:]
        observer.trace.events.clear()
        pol = policy.on_token(events)
        vecs, resident, nonres = w.residency(prev_vec)
        faulted = []
        if prev_vec is not None:
            for idx, spans in enumerate(w._spans):
                new = (vecs[idx] & 1) & ~(prev_vec[idx] & 1)
                faulted += [b for s, e, b in spans
                            if int(np.count_nonzero(new[s:e]))]
        prev_vec = vecs
        ru = resource.getrusage(resource.RUSAGE_SELF)
        fam_tot, fam_res = Counter(), Counter()
        for idx, spans in enumerate(w._spans):
            for s, e, bid in spans:
                comp = registry.blocks()[bid].component
                fam_tot[comp] += e - s
                fam_res[comp] += int(np.count_nonzero(vecs[idx][s:e] & 1))
        row = {
            "token_pos": t, "phase": phase, "majflt_cum": ru.ru_majflt,
            "rss_mb": ru.ru_maxrss / (1024 if sys.platform == "linux" else 1024**2),
            "swap_gb": round(_swapused_gb(), 3),
            "resident_frac": {c: round(fam_res[c] / fam_tot[c], 4)
                              for c in fam_tot},
            "resident_weight_bytes": sum(resident.values()) * PAGE,
            "faulted_blocks": sorted(set(faulted)),
            "nonresident_bytes": nonres, "policy": pol,
        }
        rows.append(row)
        top10 = dict(policy.freq.most_common(10))
        pol_rows.append({"token_pos": t, "freq_top": top10,
                         "pin": sorted(policy.pin),
                         "resident_bytes": policy.resident_bytes})

    prompt_ids = tokenizer.encode(prompt_text)
    if calibrate:
        max_tokens = min(max_tokens, 4)
    torch.manual_seed(42)
    def pick(lg):  # greedy (T=0) o multinomial con temperatura
        if temperature <= 0:
            return int(lg.argmax())
        p = torch.softmax(lg.float().nan_to_num(0.0, -1e4, 1e4) / temperature, -1)
        return int(torch.multinomial(p, 1))
    with observer.capture(run_cfg):
        adapter.instrument()
        with torch.no_grad():
            ids = torch.tensor([prompt_ids])
            out = model(input_ids=ids, use_cache=True)
            past = out.past_key_values
            drain("prefill", None)
            nxt = pick(out.logits[0, -1])
            eos = getattr(registry.cfg, "eos_token_id", None)
            eos_ids = set(eos) if isinstance(eos, (list, tuple)) else \
                ({eos} if eos is not None else set())
            for t in range(1, max_tokens):
                if nxt in eos_ids:
                    break
                adapter._phase, adapter._token_pos = "decode", t
                out = model(input_ids=torch.tensor([[nxt]]),
                            past_key_values=past, use_cache=True)
                past = out.past_key_values
                drain("decode", t)
                nxt = pick(out.logits[0, -1])
        adapter.remove()
    # deltas de pageins por token respecto al inicio de la corrida
    cum = [majflt0] + [r["majflt_cum"] for r in rows]
    for r, a, b in zip(rows, cum, cum[1:]):
        r["pageins_bytes"] = (b - a) * PAGE
    total = rows[-1]["majflt_cum"] - majflt0
    out_path = Path(out_dir) / run_id
    out_path.mkdir(parents=True, exist_ok=True)
    peak_rss_gb = rows[-1]["rss_mb"] / 1024
    weight_res_end_gb = rows[-1]["resident_weight_bytes"] / 1e9
    # budget efectivo = RAM − presión − anónimo (rss pico − pesos residentes)
    budget_eff_gb = ram - pressure_gb - (peak_rss_gb - weight_res_end_gb)
    report = {
        "run_id": run_id, "model_id": model_id, "policy": policy_name,
        "budget_gb": budget_gb, "slack_gb": slack_gb,
        "pressure_gb": round(pressure_gb, 2), "ram_total_gb": round(ram, 1),
        "budget_eff_gb": round(budget_eff_gb, 2),
        "fit": policy.fit, "ws_total_gb": round(policy.ws_total / 1e9, 2),
        "calibrate": calibrate,
        "tokens": len(rows), "elapsed_s": round(time.time() - t_start, 1),
        "pageins_total_bytes": total * PAGE,
        "majflt_start": majflt0,
        "majflt_end": rows[-1]["majflt_cum"],
        "n_evictions": policy.n_evictions, "n_willneed": policy.n_willneed,
        "peak_rss_gb": round(peak_rss_gb, 2),
        "weight_resident_end_gb": round(weight_res_end_gb, 2),
        "prompt_len": len(prompt_ids),
    }
    (out_path / "report.json").write_text(json.dumps(report, indent=2))
    for name, rs in (("residency", rows), ("policy", pol_rows)):
        with open(out_path / f"{name}.jsonl", "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rs)
    return report
