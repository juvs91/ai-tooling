#!/usr/bin/env python3
"""azure_mock.py — lo que registry:2 y pypiserver no hacen y ACR / Azure Artifacts sí.

  ACR (detrás de nginx, que manda aquí solo PUT/DELETE de manifests):
    - locks por tag y por digest (write-enabled / delete-enabled = false) que
      el servidor HACE CUMPLIR: un push a un tag bloqueado → 405 DENIED, como ACR.
    - /_sim/acr/locks       GET lista · POST {"image": "repo:tag"|"repo@sha256:…",
                            "write": false, "delete": false}
    - /_sim/acr/fail        POST {"mode": "auth"|""} — el shim de az lo consulta
  Azure Artifacts:
    - PATCH /{org}/[{project}/]_apis/packaging/feeds/{feed}/pypi/packages/{pkg}/versions/{v}
      promoción a view; 404 si la versión no existe en pypiserver (nombre
      normalizado PEP 503), 400 si el body no es el de la API.
    - /_sim/artifacts/calls GET — llamadas registradas
  /_sim/reset POST — limpia estado.

Solo stdlib. Variables: REGISTRY_UPSTREAM (http://registry:5000), PYPI_UPSTREAM
(http://pypi:8080), PORT (8082).
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REGISTRY = os.environ.get("REGISTRY_UPSTREAM", "http://registry:5000")
PYPI = os.environ.get("PYPI_UPSTREAM", "http://pypi:8080")
PORT = int(os.environ.get("PORT", "8082"))

LOCK = threading.Lock()
STATE: dict = {"locks": {}, "fail": "", "calls": []}

MANIFEST = re.compile(r"^/v2/(?P<repo>.+)/manifests/(?P<ref>[^/]+)$")
VIEWS = re.compile(
    r"^/(?P<org>[^/]+)/(?:(?P<project>[^/_][^/]*)/)?_apis/packaging/feeds/(?P<feed>[^/]+)"
    r"/pypi/packages/(?P<pkg>[^/]+)/versions/(?P<ver>[^/?]+)\?api-version=7\.1-preview\.1$"
)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ── utilidades ───────────────────────────────────────────────────────────
    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send(self, code: int, payload: object, headers: dict | None = None) -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _denied(self, msg: str) -> None:
        self._send(405, {"errors": [{"code": "DENIED", "message": msg}]})

    def log_message(self, fmt: str, *args) -> None:
        print(f"azure-mock: {self.command} {self.path} — " + fmt % args, flush=True)

    # ── ACR: manifests ───────────────────────────────────────────────────────
    def _manifest(self) -> bool:
        m = MANIFEST.match(self.path.split("?")[0])
        if not m:
            return False
        repo, ref = m.group("repo"), m.group("ref")
        key = f"{repo}@{ref}" if ref.startswith("sha256:") else f"{repo}:{ref}"
        lock = STATE["locks"].get(key, {})
        body = self._body()
        if self.command == "PUT" and lock.get("write") is False:
            return self._denied(f"{key} is write-disabled (locked)") or True
        if self.command == "DELETE" and lock.get("delete") is False:
            return self._denied(f"{key} is delete-disabled (locked)") or True
        req = urllib.request.Request(REGISTRY + self.path, data=body if self.command == "PUT" else None,
                                     method=self.command)
        for h in ("Content-Type", "Authorization", "Host"):
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req) as r:
                headers = {k: v for k, v in r.headers.items()
                           if k in ("Docker-Content-Digest", "Location", "Docker-Distribution-Api-Version")}
                self._send(r.status, r.read(), headers)
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
        return True

    # ── Artifacts: views ─────────────────────────────────────────────────────
    def _views(self) -> None:
        m = VIEWS.match(self.path)
        raw = self._body()
        call = {"path": self.path, "auth": self.headers.get("Authorization"), "body": raw.decode()}
        with LOCK:
            STATE["calls"].append(call)
        if not m:
            return self._send(404, {"message": "ruta de API inesperada", "path": self.path})
        if not (self.headers.get("Authorization") or "").startswith("Bearer "):
            return self._send(401, {"message": "falta Bearer token (¿SYSTEM_ACCESSTOKEN mapeado?)"})
        try:
            body = json.loads(raw)
            view = body["views"]
            assert view["op"] == "add" and view["path"] == "/views/-" and view["value"]
        except (ValueError, KeyError, AssertionError, TypeError):
            return self._send(400, {"message": "body inválido", "body": raw.decode()})
        pkg, ver = m.group("pkg"), m.group("ver")
        try:
            with urllib.request.urlopen(f"{PYPI}/simple/{_norm(pkg)}/") as r:
                index = r.read().decode()
        except urllib.error.HTTPError:
            return self._send(404, {"message": f"paquete {pkg} no existe en el feed"})
        name_re = "[-_.]".join(re.escape(part) for part in _norm(pkg).split("-"))
        if not re.search(rf"{name_re}-{re.escape(ver)}[-.]", index, re.I):
            return self._send(404, {"message": f"{pkg} {ver} no existe en el feed"})
        call["view"] = view["value"]
        call["package"], call["version"] = pkg, ver
        self._send(200, {"promoted": f"{pkg} {ver} → @{view['value']}"})

    # ── admin ────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/_sim/acr/locks":
            return self._send(200, STATE["locks"])
        if self.path == "/_sim/acr/fail":
            return self._send(200, {"mode": STATE["fail"]})
        if self.path == "/_sim/artifacts/calls":
            return self._send(200, STATE["calls"])
        if self.path == "/_sim/health":
            return self._send(200, {"ok": True})
        self._send(404, {"message": "no encontrado"})

    def do_POST(self) -> None:  # noqa: N802
        data = json.loads(self._body() or b"{}")
        with LOCK:
            if self.path == "/_sim/acr/locks":
                cur = STATE["locks"].setdefault(data["image"], {})
                for k in ("write", "delete"):
                    if k in data:
                        cur[k] = bool(data[k])
                return self._send(200, cur)
            if self.path == "/_sim/acr/fail":
                STATE["fail"] = data.get("mode", "")
                return self._send(200, {"mode": STATE["fail"]})
            if self.path == "/_sim/reset":
                STATE.update({"locks": {}, "fail": "", "calls": []})
                return self._send(200, {"reset": True})
        self._send(404, {"message": "no encontrado"})

    def do_PUT(self) -> None:  # noqa: N802
        if not self._manifest():
            self._send(404, {"message": "solo manifests"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._manifest():
            self._send(404, {"message": "solo manifests"})

    def do_PATCH(self) -> None:  # noqa: N802
        self._views()


if __name__ == "__main__":
    print(f"azure-mock en :{PORT} (registry={REGISTRY}, pypi={PYPI})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
