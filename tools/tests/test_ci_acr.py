"""
test_ci_acr.py — Suite de tests para scripts/ci-acr.sh contra un ACR simulado.

Corre con stdlib unittest:

  python3 -m unittest tools/tests/test_ci_acr.py -v

`az` y `docker` se reemplazan por un fake (Python) que guarda repos, tags,
digests y locks en un JSON y aplica los locks como ACR: un tag con
write-enabled=false no se puede re-apuntar.

Cobertura (ADR-0060):
  - ensure-ctx: build+push+lock si no existe; sin rebuild si existe;
    un error de auth al listar NO se interpreta como "no existe" (no rebuild).
  - promote: re-tag sin rebuild con el mismo digest; idempotente en re-run;
    falla si el tag destino existe con otro digest; falla si el origen no existe.
  - flujo completo dev → rc → prod: los tres tags comparten el digest del ctx.
  - locks: tag y manifest bloqueados por separado.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "ci-acr.sh"

FAKE = textwrap.dedent(
    r'''
    #!/usr/bin/env python3
    """Fake de az/docker para ci-acr.sh. Estado en $FAKE_ACR_STATE."""
    import hashlib, json, os, sys
    STATE = os.environ["FAKE_ACR_STATE"]
    def load():
        with open(STATE) as f: return json.load(f)
    def save(s):
        with open(STATE, "w") as f: json.dump(s, f)
    def log(s, *a): s["calls"].append(list(a))
    def arg(a, flag):
        return a[a.index(flag) + 1]
    def split_ref(ref):
        ref = ref.split("/", 1)[1] if "/" in ref and "." in ref.split("/", 1)[0] else ref
        if "@" in ref: repo, d = ref.split("@", 1); return repo, None, d
        repo, tag = ref.rsplit(":", 1); return repo, tag, None
    tool, a = os.path.basename(sys.argv[0]), sys.argv[1:]
    s = load(); log(s, tool, *a); save(s)
    fail = os.environ.get("FAKE_FAIL", "")
    if tool == "az":
        sub = " ".join(a[:3])
        if a[:2] == ["acr", "login"]: sys.exit(0)
        if sub == "acr repository list":
            if fail == "list": print("ERROR: AuthorizationFailed", file=sys.stderr); sys.exit(1)
            print("\n".join(s["repos"].keys())); sys.exit(0)
        if sub == "acr repository show-tags":
            print("\n".join(s["repos"].get(arg(a, "--repository"), {}).keys())); sys.exit(0)
        if sub == "acr repository show":
            repo, tag, _ = split_ref(arg(a, "--image"))
            d = s["repos"].get(repo, {}).get(tag)
            if not d: print("ERROR: not found", file=sys.stderr); sys.exit(1)
            print(d); sys.exit(0)
        if sub == "acr repository update":
            s["locks"].append(arg(a, "--image")); save(s); sys.exit(0)
    if tool == "docker":
        if a[0] == "build": sys.exit(0)
        if a[0] == "push":
            repo, tag, _ = split_ref(a[1])
            if f"{repo}:{tag}" in s["locks"]: print("denied: locked", file=sys.stderr); sys.exit(1)
            s["pushes"] += 1
            d = "sha256:" + hashlib.sha256(f"{a[1]}#{s['pushes']}".encode()).hexdigest()
            s["repos"].setdefault(repo, {})[tag] = d; save(s); sys.exit(0)
        if a[:3] == ["buildx", "imagetools", "create"]:
            repo, tag, _ = split_ref(arg(a, "--tag"))
            _, _, d = split_ref(a[-1])
            if f"{repo}:{tag}" in s["locks"]: print("denied: locked", file=sys.stderr); sys.exit(1)
            s["repos"].setdefault(repo, {})[tag] = d; save(s); sys.exit(0)
    print(f"fake: comando no soportado: {tool} {a}", file=sys.stderr); sys.exit(99)
    '''
).lstrip()

ACR, REPO = "metalsaacr", "ventas/api"
CTX = "ctx-0123456789abcdef"


class AcrTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.state = tmp / "state.json"
        self.state.write_text(json.dumps({"repos": {}, "locks": [], "calls": [], "pushes": 0}))
        bindir = tmp / "bin"
        bindir.mkdir()
        for tool in ("az", "docker"):
            p = bindir / tool
            p.write_text(FAKE)
            p.chmod(0o755)
        self.bindir = bindir
        self.dockerfile = tmp / "Dockerfile"
        self.dockerfile.write_text("FROM scratch\n")
        self.ctxdir = tmp

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_acr(self, *args: str, fail: str = "") -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.bindir}:{os.environ['PATH']}",
            "FAKE_ACR_STATE": str(self.state),
            "FAKE_FAIL": fail,
        }
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)

    def st(self) -> dict:
        return json.loads(self.state.read_text())

    def ensure(self, fail: str = "") -> subprocess.CompletedProcess[str]:
        return self.run_acr("ensure-ctx", ACR, REPO, CTX, str(self.dockerfile), str(self.ctxdir),
                            "--label", "org.opencontainers.image.revision=abc", fail=fail)

    def builds(self) -> int:
        return sum(1 for c in self.st()["calls"] if c[:2] == ["docker", "build"])


class TestEnsureCtx(AcrTestCase):
    def test_builds_pushes_and_locks_when_missing(self) -> None:
        r = self.ensure()
        self.assertEqual(r.returncode, 0, r.stderr)
        digest = r.stdout.strip()
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(self.builds(), 1)
        self.assertIn(f"{REPO}:{CTX}", self.st()["locks"])
        self.assertIn(f"{REPO}@{digest}", self.st()["locks"])
        build = next(c for c in self.st()["calls"] if c[:2] == ["docker", "build"])
        self.assertIn("org.opencontainers.image.revision=abc", build)
        self.assertIn(f"{ACR}.azurecr.io/{REPO}:{CTX}", build)

    def test_no_rebuild_when_present(self) -> None:
        first = self.ensure().stdout.strip()
        second = self.ensure()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), first)
        self.assertEqual(self.builds(), 1)
        self.assertIn("sin rebuild", second.stderr)

    def test_auth_error_is_not_treated_as_missing(self) -> None:
        r = self.ensure(fail="list")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.builds(), 0)
        self.assertIn("no se pudo listar", r.stderr)

    def test_invalid_ctx_tag(self) -> None:
        r = self.run_acr("ensure-ctx", ACR, REPO, "latest", str(self.dockerfile), ".")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.builds(), 0)


class TestPromote(AcrTestCase):
    def test_full_flow_dev_rc_prod_same_digest(self) -> None:
        digest = self.ensure().stdout.strip()
        for src, dst in ((CTX, "1.2.0-dev.41"), (CTX, "1.2.0-rc.1"), ("1.2.0-rc.1", "1.2.0")):
            with self.subTest(dst=dst):
                r = self.run_acr("promote", ACR, REPO, src, dst)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip(), digest)
                self.assertEqual(self.st()["repos"][REPO][dst], digest)
                self.assertIn(f"{REPO}:{dst}", self.st()["locks"])
        self.assertEqual(self.builds(), 1, "build once, promote many")

    def test_idempotent_rerun(self) -> None:
        self.ensure()
        self.assertEqual(self.run_acr("promote", ACR, REPO, CTX, "1.2.0-rc.1").returncode, 0)
        creates = lambda: sum(1 for c in self.st()["calls"] if c[:3] == ["docker", "buildx", "imagetools"])
        before = creates()
        r = self.run_acr("promote", ACR, REPO, CTX, "1.2.0-rc.1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(creates(), before)

    def test_refuses_to_repoint_existing_tag(self) -> None:
        self.ensure()
        st = self.st()
        st["repos"][REPO]["1.2.0"] = "sha256:" + "f" * 64
        self.state.write_text(json.dumps(st))
        r = self.run_acr("promote", ACR, REPO, CTX, "1.2.0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("inmutables", r.stderr)
        self.assertEqual(self.st()["repos"][REPO]["1.2.0"], "sha256:" + "f" * 64)

    def test_missing_source(self) -> None:
        r = self.run_acr("promote", ACR, REPO, "1.2.0-rc.9", "1.2.0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no hay imagen que promover", r.stderr)


class TestDigestAndCli(AcrTestCase):
    def test_digest(self) -> None:
        digest = self.ensure().stdout.strip()
        r = self.run_acr("digest", ACR, f"{REPO}:{CTX}")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, digest))
        self.assertEqual(self.run_acr("digest", ACR, f"{REPO}:nope").returncode, 1)

    def test_usage(self) -> None:
        self.assertEqual(self.run_acr("nope").returncode, 2)
        self.assertEqual(self.run_acr("promote", ACR).returncode, 2)


if __name__ == "__main__":
    unittest.main()
