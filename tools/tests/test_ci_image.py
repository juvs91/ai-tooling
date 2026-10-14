"""
test_ci_image.py — Suite de tests para scripts/ci-image.sh.

Corre con stdlib unittest (sin dependencias externas salvo git y PyYAML para
check-paths):

  python3 -m unittest tools/tests/test_ci_image.py -v

Cobertura (escenarios reales del monorepo GitOps, ADR-0007 / ADR-0060):
  - context-hash: estable ante commits de OTROS servicios (el caso que rompía
    "imagen por commit"), cambia si cambia el servicio o una lib compartida,
    independiente del orden de las rutas, error si la ruta no existe.
  - parse-tag: dev/rc/hotfix/prod, refs/tags/…, --expect, --azure, tags fuera
    de convención.
  - prod-source: rc más alto con orden numérico (rc.10 > rc.2), error sin rc.
  - same-content: prod en HEAD de trunk con commits ajenos → OK; con cambio
    en el servicio desde el rc → falla.
  - check-paths: trigger.paths.include cubre / no cubre las dependencias.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "ci-image.sh"


class _Repo:
    """Repo git temporal con layout iniciativa/servicio + _shared/libs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(self, rel: str, content: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def commit(self, msg: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def tag(self, name: str, ref: str = "HEAD") -> None:
        self.git("tag", "-a", name, "-m", name, ref)


def run(repo: _Repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=repo.root,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )


SVC = ("ventas/api", "_shared/libs/auth")


class CiImageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _Repo(Path(self._tmp.name))
        self.repo.write("ventas/api/pyproject.toml", 'version = "1.0.0"\n')
        self.repo.write("ventas/api/Dockerfile", "FROM python:3.12-slim\n")
        self.repo.write("ventas/web/package.json", '{"version":"1.0.0"}\n')
        self.repo.write("_shared/libs/auth/pyproject.toml", 'version = "0.1.0"\n')
        self.base = self.repo.commit("init")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def hash(self, ref: str = "HEAD", paths: tuple[str, ...] = SVC) -> str:
        r = run(self.repo, "context-hash", "--ref", ref, *paths)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()


class TestContextHash(CiImageTestCase):
    def test_format(self) -> None:
        self.assertRegex(self.hash(), r"^ctx-[0-9a-f]{16}$")

    def test_stable_across_commits_of_other_services(self) -> None:
        before = self.hash()
        self.repo.write("ventas/web/index.ts", "export {}\n")
        self.repo.commit("feat(web): otro servicio")
        self.assertEqual(self.hash(), before)

    def test_changes_when_service_changes(self) -> None:
        before = self.hash()
        self.repo.write("ventas/api/app.py", "print(1)\n")
        self.repo.commit("feat(api)")
        self.assertNotEqual(self.hash(), before)

    def test_changes_when_shared_lib_changes(self) -> None:
        before = self.hash()
        self.repo.write("_shared/libs/auth/token.py", "X = 1\n")
        self.repo.commit("feat(auth)")
        self.assertNotEqual(self.hash(), before)

    def test_order_and_trailing_slash_independent(self) -> None:
        a = self.hash(paths=("ventas/api", "_shared/libs/auth"))
        b = self.hash(paths=("_shared/libs/auth/", "ventas/api/"))
        self.assertEqual(a, b)

    def test_historic_ref(self) -> None:
        before = self.hash()
        self.repo.write("ventas/api/app.py", "print(1)\n")
        self.repo.commit("feat(api)")
        self.assertEqual(self.hash(ref=self.base), before)

    def test_missing_path_fails(self) -> None:
        r = run(self.repo, "context-hash", "ventas/nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no existe", r.stderr)


class TestParseTag(CiImageTestCase):
    def parse(self, *args: str) -> dict[str, str]:
        r = run(self.repo, "parse-tag", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line and not line.startswith("##"))

    def test_envs(self) -> None:
        cases = {
            "ventas-api@1.2.0-dev.7": ("dev", "1.2.0-dev.7"),
            "ventas-api@1.2.0-rc.2": ("qa", "1.2.0-rc.2"),
            "ventas-api@1.2.0-hotfix.1": ("hotfix", "1.2.0-hotfix.1"),
            "ventas-api@1.2.0": ("prod", "1.2.0"),
        }
        for tag, (env, image_tag) in cases.items():
            with self.subTest(tag=tag):
                out = self.parse(tag)
                self.assertEqual(out["name"], "ventas-api")
                self.assertEqual(out["version"], "1.2.0")
                self.assertEqual(out["env"], env)
                self.assertEqual(out["image_tag"], image_tag)

    def test_refs_prefix(self) -> None:
        self.assertEqual(self.parse("refs/tags/ventas-api@1.2.0")["env"], "prod")

    def test_expect_mismatch(self) -> None:
        r = run(self.repo, "parse-tag", "--expect", "ventas-web", "ventas-api@1.2.0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("este pipeline es de 'ventas-web'", r.stderr)

    def test_azure_output(self) -> None:
        r = run(self.repo, "parse-tag", "--azure", "ventas-api@1.2.0-rc.3")
        self.assertIn("##vso[task.setvariable variable=env;isOutput=true]qa", r.stdout)

    def test_invalid(self) -> None:
        for tag in ("ventas-api@1.2", "ventas-api@1.2.0-beta.1", "ventas-api@1.2.0-rc", "Ventas@1.0.0", "1.0.0"):
            with self.subTest(tag=tag):
                self.assertEqual(run(self.repo, "parse-tag", tag).returncode, 1)


class TestProdSource(CiImageTestCase):
    def test_numeric_order(self) -> None:
        for n in (1, 2, 10):
            self.repo.tag(f"ventas-api@1.2.0-rc.{n}")
        self.repo.tag("ventas-api@1.3.0-rc.99")
        r = run(self.repo, "prod-source", "ventas-api", "1.2.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "ventas-api@1.2.0-rc.10")

    def test_no_rc(self) -> None:
        self.repo.tag("ventas-api@1.2.0-dev.1")
        r = run(self.repo, "prod-source", "ventas-api", "1.2.0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("rc aprobado en QA", r.stderr)


class TestNextHotfix(CiImageTestCase):
    def test_increments_numerically(self) -> None:
        self.repo.tag("ventas-api@1.2.0")
        self.assertEqual(run(self.repo, "next-hotfix", "ventas-api", "1.2.0").stdout.strip(), "ventas-api@1.2.0-hotfix.1")
        for n in (1, 2, 10):
            self.repo.tag(f"ventas-api@1.2.0-hotfix.{n}")
        self.assertEqual(run(self.repo, "next-hotfix", "ventas-api", "1.2.0").stdout.strip(), "ventas-api@1.2.0-hotfix.11")

    def test_requires_prod_tag(self) -> None:
        r = run(self.repo, "next-hotfix", "ventas-api", "1.2.0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("parte de un tag de prod", r.stderr)


class TestSameContent(CiImageTestCase):
    def test_prod_on_trunk_head_after_unrelated_commits(self) -> None:
        self.repo.tag("ventas-api@1.2.0-rc.1")
        self.repo.write("ventas/web/index.ts", "export {}\n")
        self.repo.commit("feat(web)")
        r = run(self.repo, "same-content", "ventas-api@1.2.0-rc.1", "HEAD", *SVC)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_service_changed_since_rc(self) -> None:
        self.repo.tag("ventas-api@1.2.0-rc.1")
        self.repo.write("_shared/libs/auth/token.py", "X = 2\n")
        self.repo.commit("feat(auth)")
        r = run(self.repo, "same-content", "ventas-api@1.2.0-rc.1", "HEAD", *SVC)
        self.assertEqual(r.returncode, 1)
        self.assertIn("token.py", r.stderr)


class TestCheckPaths(CiImageTestCase):
    def test_covered_and_missing(self) -> None:
        self.repo.write(
            "pipelines/services/ventas-api.yml",
            "trigger:\n  branches:\n    include: [main]\n  paths:\n    include:\n      - ventas/api\n      - _shared/libs\n",
        )
        ok = run(self.repo, "check-paths", "pipelines/services/ventas-api.yml", *SVC)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = run(self.repo, "check-paths", "pipelines/services/ventas-api.yml", "ventas/api", "ventas/web")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("ventas/web", bad.stderr)


class TestManifestVersion(CiImageTestCase):
    def test_sources(self) -> None:
        self.repo.write("ventas/api/pyproject.toml", '[project]\nname = "api"\nversion = "1.4.2"\n')
        self.repo.write("ventas/web/package.json", '{"name":"web","version":"2.0.1"}')
        self.repo.write("ventas/job/VERSION", "0.3.0\n")
        for path, want in (("ventas/api", "1.4.2"), ("ventas/web/", "2.0.1"), ("ventas/job", "0.3.0")):
            with self.subTest(path=path):
                r = run(self.repo, "manifest-version", path)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, want), r.stderr)

    def test_invalid(self) -> None:
        self.repo.write("ventas/api/pyproject.toml", '[project]\nversion = "1.4"\n')
        self.assertEqual(run(self.repo, "manifest-version", "ventas/api").returncode, 1)
        self.assertEqual(run(self.repo, "manifest-version", "ventas/nada").returncode, 1)


class TestPep440(CiImageTestCase):
    def test_conversions(self) -> None:
        for tag, want in (("auth@1.2.0", "1.2.0"), ("auth@1.2.0-dev.41", "1.2.0.dev41"),
                          ("refs/tags/auth@1.2.0-rc.3", "1.2.0rc3")):
            with self.subTest(tag=tag):
                r = run(self.repo, "pep440", tag)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, want), r.stderr)

    def test_hotfix_rejected(self) -> None:
        r = run(self.repo, "pep440", "auth@1.2.0-hotfix.1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("patch nuevo", r.stderr)


class TestCli(CiImageTestCase):
    def test_usage_errors(self) -> None:
        self.assertEqual(run(self.repo, "nope").returncode, 2)
        self.assertEqual(run(self.repo, "context-hash").returncode, 2)


if __name__ == "__main__":
    unittest.main()
