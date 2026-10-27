"""Ollama probe — observación EXTERNA de Ollama (Fase 0.5, PRD §4, ADR-0061).

Ollama es caja negra: nada se instrumenta por dentro. Se miden dos planos:
  server_metric — API /api/generate (stream): eval/prompt_eval/load durations
  os_metric     — vm_stat (pageins/swap) + RSS del runner, hilo cada 250ms

Semántica de campos en eventos phase="request" (no hay bloques; el
metadata de cada corrida lleva metric_semantics):
  server_metric: size_bytes = eval_count (tokens generados),
                 duration_ns = eval_duration (decode del turno)
  os_metric:     block_id = "host.<métrica>", size_bytes = valor en bytes
                 (RSS; contadores pageins/swap = páginas × page_size)
"""

import json
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field

from .events import Event

OLLAMA_HOST = "http://localhost:11434"
OS_METRICS = ("rss", "pageins", "swapins", "swapouts", "swapused")


def _run(cmd: list[str]) -> str | None:
    """stdout de un comando corto, o None si falla/vacío."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return out.stdout if out.returncode == 0 and out.stdout.strip() else None
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def _request(host: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    return urllib.request.Request(host + path, data=data,
                                  headers={"Content-Type": "application/json"})


def http_json(path: str, payload: dict | None = None,
              host: str = OLLAMA_HOST, timeout: int = 3600):
    with urllib.request.urlopen(_request(host, path, payload),
                                timeout=timeout) as r:
        return json.loads(r.read())


def runner_pid() -> int | None:
    """PID del proceso runner (existe solo con modelo cargado)."""
    for pattern in ("llama-server", "ollama runner", "runners/"):
        out = _run(["pgrep", "-nf", pattern])
        if out:
            try:
                return int(out.split()[0])
            except ValueError:
                continue
    return None


def _rss_bytes(pid: int | None) -> int | None:
    if pid is None:
        return None
    out = _run(["ps", "-o", "rss=", "-p", str(pid)])
    return int(out.split()[0]) * 1024 if out else None


def parse_vm_stat(text: str) -> dict:
    """vm_stat (macOS) → contadores en bytes. Linux → {} (port: Fase 1)."""
    counters, page_size = {}, 16384
    for line in text.splitlines():
        if "page size of" in line:
            digits = "".join(c for c in line if c.isdigit())
            page_size = int(digits) if digits else page_size
        elif ":" in line:
            key, _, rest = line.partition(":")
            num = "".join(c for c in rest if c.isdigit() or c == ".")
            if num:
                counters[key.strip().lower()] = int(float(num)) * page_size
    return counters


def vm_stat() -> dict:
    out = _run(["vm_stat"])
    return parse_vm_stat(out) if out else {}


def swapused_bytes() -> int | None:
    """vm_stat no reporta swap usado; sysctl vm.swapusage sí (macOS)."""
    out = _run(["sysctl", "vm.swapusage"])
    if not out:
        return None
    try:
        for part in out.replace("=", " ").split():
            if part.endswith("M"):
                return int(float(part[:-1]) * 2**20)
            if part.endswith("G"):
                return int(float(part[:-1]) * 2**30)
    except ValueError:
        pass
    return None


def sample_os(pid: int | None = None) -> dict:
    """Snapshot único: RSS del runner + contadores de memoria del host."""
    vm = vm_stat()
    return {"rss": _rss_bytes(pid),
            "pageins": vm.get("pageins"),
            "swapins": vm.get("swapins"),
            "swapouts": vm.get("swapouts"),
            "swapused": swapused_bytes()}


@dataclass
class OsSampler:
    """Hilo daemon: sample_os() cada interval_s hasta stop()."""
    interval_s: float = 0.25
    samples: list = field(default_factory=list)  # (monotonic_ns, dict)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self, pid: int | None = None) -> None:
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                # resolución perezosa: el runner no existe hasta que el
                # modelo empieza a cargar (pid=None en las primeras muestras)
                self.samples.append((time.monotonic_ns(),
                                     sample_os(pid if pid is not None
                                               else runner_pid())))
                self._stop.wait(self.interval_s)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2 * self.interval_s + 1)


def run_turn(model: str, prompt: str, num_ctx: int, num_predict: int,
             keep_alive: str = "30m", host: str = OLLAMA_HOST,
             sampler: OsSampler | None = None) -> tuple[dict, str]:
    """Un turno de generación; devuelve (métricas finales del stream, texto).

    El mensaje con done=true lleva: eval_count/eval_duration (decode),
    prompt_eval_count/prompt_eval_duration (prefill), load_duration."""
    payload = {"model": model, "prompt": prompt, "stream": True,
               "options": {"num_ctx": num_ctx, "temperature": 0,
                           "num_predict": num_predict},
               "keep_alive": keep_alive}
    req = _request(host, "/api/generate", payload)
    metrics, text = {}, []
    if sampler:
        sampler.start(runner_pid())
    try:
        with urllib.request.urlopen(req, timeout=3600) as r:
            for raw in r:
                if not raw.strip():
                    continue
                chunk = json.loads(raw)
                if chunk.get("response"):
                    text.append(chunk["response"])
                if chunk.get("done"):
                    metrics = chunk
    finally:
        if sampler:
            sampler.stop()
    return metrics, "".join(text)


def to_events(run_id: str, metrics: dict, samples: list,
              now_ns) -> list[Event]:
    """Eventos phase="request": 1 server_metric + 1 os_metric por muestra×métrica."""
    events = [Event(now_ns(), run_id, None, "request", None, "OTHER", None,
                    "EXECUTE", metrics.get("eval_count"),
                    metrics.get("eval_duration"), "server_metric")]
    for ts, snap in samples:
        for metric in OS_METRICS:
            value = snap.get(metric)
            if value is not None:
                events.append(Event(ts, run_id, None, "request", None, "OTHER",
                                    f"host.{metric}", "LOGICAL_ACCESS", value,
                                    None, "os_metric"))
    return events


def unload(model: str, host: str = OLLAMA_HOST) -> None:
    http_json("/api/generate", {"model": model, "prompt": "", "keep_alive": 0},
              host=host)


METRIC_SEMANTICS = {
    "server_metric": "size_bytes=tokens generados (eval_count), "
                     "duration_ns=eval_duration (decode del turno)",
    "os_metric": "block_id=host.<métrica>, size_bytes=valor en bytes "
                 "(rss del runner; pageins/swapins/swapouts en bytes; "
                 "swapused vía sysctl vm.swapusage). Contadores acumulativos: "
                 "los deltas se computan en el reporte.",
}
