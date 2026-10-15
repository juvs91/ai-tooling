"""
test_ci_service.py — Suite de tests para scripts/ci-service.sh.

Corre con stdlib unittest:

  python3 -m unittest discover -s tools/tests -p test_ci_service.py -v

Git real (repo temporal + origin bare); `az` y `docker` son el fake de
test_ci_acr.py (ACR en un JSON, locks aplicados como ACR).

Cobertura (ADR-0060 / ADR-0061):
  - contrato de entrada: ci_require falla con exit 2 si falta una variable;
  - context publica ##vso ctx igual al hash de ci-image.sh;
  - validate-build: docker build con el Dockerfile por defecto, sin push;
  - publish-ctx: build+push+lock y salida digest con labels OCI;
  - tag-dev: crea y empuja <svc>@<versión>-dev.<BuildId>; si la versión ya
    está en prod solo avisa;
  - tag-hotfix: crea -hotfix.<N+1> y avisa del cherry obligatorio;
  - plan-release: dev → build desde el contenido del tag; prod → digest del
    último rc sin build; prod con contenido distinto al rc → falla;
  - publish-release: flujo dev → rc → prod con el mismo digest;
  - validate-tag rechaza tags de otro servicio.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ci_acr import FAKE  # noqa: E402
from test_ci_image import _Repo  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "ci-service.sh"
VSO = re.compile(r"^##vso\[task\.setvariable variable=(\w+);isOutput=true\](.*)$", re.M)

BASE_ENV = {
    "CI_SERVICE_NAME": "ventas-api",
    "CI_SERVICE_PATH": "ventas/api",
    "CI_DEPENDS_ON": "_shared/libs/auth",
    "CI_ACR_NAME": "metalsaacr",
    "CI_IMAGE_REPOSITORY": "ventas/api",
    "CI_BUILD_CONTEXT": ".",
    "BUILD_BUILDID": "42",
    "BUILD_BUILDNUMBER": "20261015.1",
    "BUILD_SOURCEVERSION": "abc123",
    "BUILD_REPOSITORY_URI": "https://dev.azure.com/org/p/_git/repo",
}


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.origin = tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        (tmp / "repo").mkdir()
        self.repo = _Repo(tmp / "repo")
        self.repo.git("remote", "add", "origin", str(self.origin))
        self.repo.write("ventas/api/pyproject.toml", '[project]\nname = "ventas-api"\nversion = "1.0.0"\n')
        self.repo.write("ventas/api/Dockerfile", "FROM scratch\n")
        self.repo.write("ventas/web/package.json", '{"version":"1.0.0"}\n')
        self.repo.write("_shared/libs/auth/pyproject.toml", 'version = "0.1.0"\n')
        self.repo.commit("init")
        self.repo.git("push", "-q", "origin", "main")

        self.state = tmp / "state.json"
        self.state.write_text(json.dumps({"repos": {}, "locks": [], "calls": [], "pushes": 0}))
        self.bindir = tmp / "bin"
        self.bindir.mkdir()
        for tool in ("az", "docker"):
            (self.bindir / tool).write_text(FAKE)
            (self.bindir / tool).chmod(0o755)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_svc(self, cmd: str, **extra: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        env = {**os.environ, **BASE_ENV, **extra,
               "PATH": f"{self.bindir}:{os.environ['PATH']}",
               "FAKE_ACR_STATE": str(self.state), "LC_ALL": "C"}
        env = {k: v for k, v in env.items() if v is not None}
        r = subprocess.run(["bash", str(SCRIPT), cmd], cwd=self.repo.root,
                           capture_output=True, text=True, env=env)
        return r, dict(VSO.findall(r.stdout))

    def ok(self, cmd: str, **extra: str) -> dict[str, str]:
        r, out = self.run_svc(cmd, **extra)
        self.assertEqual(r.returncode, 0, f"{cmd}\nstdout:{r.stdout}\nstderr:{r.stderr}")
        return out

    def st(self) -> dict:
        return json.loads(self.state.read_text())

    def builds(self) -> int:
        return sum(1 for c in self.st()["calls"] if c[:2] == ["docker", "build"])

    def remote_tags(self) -> set[str]:
        out = subprocess.run(["git", "ls-remote", "--tags", str(self.origin)],
                             capture_output=True, text=True, check=True).stdout
        return {line.split("refs/tags/")[1] for line in out.splitlines() if not line.endswith("^{}")}

    def release(self, tag: str) -> dict[str, str]:
        """Simula el pipeline de release completo para un tag: Resolve → Publish."""
        self.repo.tag(tag)
        branch = f"refs/tags/{tag}"
        t = self.ok("validate-tag", BUILD_SOURCEBRANCH=branch)
        plan = self.ok("plan-release", BUILD_SOURCEBRANCH=branch, CI_TAG_ENV=t["env"],
                       CI_TAG_NAME=t["name"], CI_TAG_VERSION=t["version"])
        return self.ok("publish-release", CI_ENV=t["env"], CI_IMAGE_TAG=t["image_tag"],
                       CI_SOURCE=plan["source"], CI_NEEDS_BUILD=plan["needsBuild"])


class TestContract(ServiceTestCase):
    def test_missing_input_is_usage_error(self) -> None:
        r, _ = self.run_svc("context", CI_SERVICE_PATH="")
        self.assertEqual(r.returncode, 2)
        self.assertIn("CI_SERVICE_PATH", r.stderr)

    def test_unknown_command(self) -> None:
        r, _ = self.run_svc("nope")
        self.assertEqual(r.returncode, 2)


class TestCiJob(ServiceTestCase):
    def test_context_matches_ci_image(self) -> None:
        out = self.ok("context")
        expected = subprocess.run(
            ["bash", str(SCRIPT.parent / "ci-image.sh"), "context-hash", "ventas/api", "_shared/libs/auth"],
            cwd=self.repo.root, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out["ctx"], expected)

    def test_validate_build_uses_default_dockerfile_and_never_pushes(self) -> None:
        self.ok("validate-build", CI_CTX="ctx-1")
        builds = [c for c in self.st()["calls"] if c[:2] == ["docker", "build"]]
        self.assertEqual(len(builds), 1)
        self.assertIn("ventas/api/Dockerfile", builds[0])
        self.assertIn("validate/ventas/api:ctx-1", builds[0])
        self.assertFalse(any(c[:2] == ["docker", "push"] for c in self.st()["calls"]))

    def test_publish_ctx_pushes_locks_and_labels(self) -> None:
        ctx = self.ok("context")["ctx"]
        out = self.ok("publish-ctx", CI_CTX=ctx)
        self.assertTrue(out["digest"].startswith("sha256:"))
        self.assertIn(f"ventas/api:{ctx}", self.st()["locks"])
        build = next(c for c in self.st()["calls"] if c[:2] == ["docker", "build"])
        self.assertIn("org.opencontainers.image.revision=abc123", build)
        self.assertIn(f"gitops.context={ctx}", build)
        # re-run: sin rebuild, mismo digest
        self.assertEqual(self.ok("publish-ctx", CI_CTX=ctx)["digest"], out["digest"])
        self.assertEqual(self.builds(), 1)

    def test_tag_dev_creates_and_pushes(self) -> None:
        self.ok("tag-dev")
        self.assertIn("ventas-api@1.0.0-dev.42", self.remote_tags())

    def test_tag_dev_skips_when_version_already_in_prod(self) -> None:
        self.repo.tag("ventas-api@1.0.0")
        r, _ = self.run_svc("tag-dev")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("##vso[task.logissue type=warning]", r.stdout)
        self.assertNotIn("ventas-api@1.0.0-dev.42", self.remote_tags())

    def test_tag_hotfix_increments_and_warns_cherry(self) -> None:
        self.repo.tag("ventas-api@1.0.0")
        self.repo.tag("ventas-api@1.0.0-hotfix.1")
        r, _ = self.run_svc("tag-hotfix", BUILD_SOURCEBRANCHNAME="fix-login")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ventas-api@1.0.0-hotfix.2", self.remote_tags())
        self.assertIn("release.sh cherry ventas-api 1.0.0", r.stdout)


class TestRelease(ServiceTestCase):
    def test_validate_tag_rejects_other_service(self) -> None:
        self.repo.tag("auth@0.1.0")
        r, _ = self.run_svc("validate-tag", BUILD_SOURCEBRANCH="refs/tags/auth@0.1.0")
        self.assertEqual(r.returncode, 1)

    def test_dev_rc_prod_share_digest(self) -> None:
        dev = self.release("ventas-api@1.0.0-dev.1")
        rc = self.release("ventas-api@1.0.0-rc.1")
        # commit ajeno al servicio entre rc y prod: prod se taggea en HEAD de trunk
        self.repo.write("ventas/web/package.json", '{"version":"1.1.0"}\n')
        self.repo.commit("web change")
        prod = self.release("ventas-api@1.0.0")
        self.assertEqual(dev["digest"], rc["digest"])
        self.assertEqual(rc["digest"], prod["digest"])
        self.assertEqual(self.builds(), 1, "una sola construcción para los tres ambientes")
        self.assertTrue(prod["imageRef"].startswith("metalsaacr.azurecr.io/ventas/api@sha256:"))

    def test_prod_plan_uses_rc_without_build(self) -> None:
        self.release("ventas-api@1.0.0-rc.1")
        self.repo.tag("ventas-api@1.0.0")
        plan = self.ok("plan-release", BUILD_SOURCEBRANCH="refs/tags/ventas-api@1.0.0",
                       CI_TAG_ENV="prod", CI_TAG_NAME="ventas-api", CI_TAG_VERSION="1.0.0")
        self.assertEqual(plan["needsBuild"], "false")
        self.assertEqual(plan["source"], "1.0.0-rc.1")

    def test_prod_fails_if_service_changed_since_rc(self) -> None:
        self.release("ventas-api@1.0.0-rc.1")
        self.repo.write("ventas/api/app.py", "print('cambio')\n")
        self.repo.commit("api change after rc")
        self.repo.tag("ventas-api@1.0.0")
        r, _ = self.run_svc("plan-release", BUILD_SOURCEBRANCH="refs/tags/ventas-api@1.0.0",
                            CI_TAG_ENV="prod", CI_TAG_NAME="ventas-api", CI_TAG_VERSION="1.0.0")
        self.assertEqual(r.returncode, 1)

    def test_deploy_placeholder(self) -> None:
        r, _ = self.run_svc("deploy-placeholder", CI_ENVIRONMENT="ventas-api-dev", CI_IMAGE_REF="x@sha256:1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("ventas-api-dev: x@sha256:1", r.stdout)


if __name__ == "__main__":
    unittest.main()
