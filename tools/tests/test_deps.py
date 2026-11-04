"""
test_deps.py — Suite de tests para scripts/deps.py (ADR-0080).

Corre con stdlib unittest:

  python3 -m unittest discover -s tools/tests -p test_deps.py -v

Cada test arma un repo git temporal real. Cobertura:
  - pyproject.toml con [tool.uv.sources] path y workspace;
  - package.json con file:/link: y workspace:*;
  - go.mod con replace (línea y bloque);
  - deps.txt con comentarios;
  - cadena transitiva y ciclos;
  - consumers (where-used), incluido un manifiesto roto en otra carpeta;
  - --ref contra un tag viejo;
  - servicio fuera del sparse checkout (lee de git, no del disco);
  - errores: carpeta inexistente, ruta fuera del repo, uso incorrecto, Python < 3.11;
  - trigger-block y policy-filter.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "deps.py"

PY_LIB = '[project]\nname = "metalsa-{name}"\nversion = "0.1.0"\n'


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout


class DepsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        self.write({"README.md": "monorepo\n"})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, files: dict[str, str], msg: str = "c") -> None:
        for rel, content in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", msg)

    def run_deps(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo,
                              capture_output=True, text=True)

    def lines(self, *args: str) -> list[str]:
        r = self.run_deps(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def libs(self, names: list[str]) -> None:
        self.write({f"_shared/libs/{n}/pyproject.toml": PY_LIB.format(name=n) for n in names})


class TestManifests(DepsTestCase):
    def test_pyproject_path_and_workspace(self) -> None:
        self.libs(["auth", "logging"])
        self.write({"ventas/api/pyproject.toml": (
            '[project]\nname = "ventas-api"\ndependencies = ["metalsa-auth", "metalsa-logging"]\n'
            '[tool.uv.sources]\nmetalsa-auth = { path = "../../_shared/libs/auth", editable = true }\n'
            'metalsa_logging = { workspace = true }\nrequests = { index = "pypi" }\n')})
        self.assertEqual(self.lines("paths", "ventas/api"),
                         ["_shared/libs/auth", "_shared/libs/logging", "ventas/api"])
        self.assertEqual(self.lines("libs", "ventas/api"), ["_shared/libs/auth", "_shared/libs/logging"])

    def test_package_json_file_link_and_workspace(self) -> None:
        self.write({
            "_shared/libs/ui/package.json": '{"name": "@metalsa/ui"}',
            "_shared/libs/fmt/package.json": '{"name": "@metalsa/fmt"}',
            "_shared/libs/icons/package.json": '{"name": "@metalsa/icons"}',
            "ventas/web/package.json": (
                '{"name": "ventas-web", "dependencies": {"@metalsa/ui": "workspace:*", "react": "18.3.1"},'
                ' "devDependencies": {"@metalsa/fmt": "file:../../_shared/libs/fmt"},'
                ' "optionalDependencies": {"@metalsa/icons": "link:../../_shared/libs/icons"}}'),
        })
        self.assertEqual(self.lines("libs", "ventas/web"),
                         ["_shared/libs/fmt", "_shared/libs/icons", "_shared/libs/ui"])

    def test_go_mod_replace_line_and_block(self) -> None:
        self.write({
            "_shared/libs/goauth/go.mod": "module example.com/goauth\n",
            "_shared/libs/golog/go.mod": "module example.com/golog\n",
            "ventas/worker/go.mod": (
                "module example.com/worker\n\n"
                "replace example.com/goauth => ../../_shared/libs/goauth\n"
                "replace (\n\texample.com/golog v0.1.0 => ../../_shared/libs/golog\n"
                "\texample.com/x => example.com/y v1.2.3\n)\n"),
        })
        self.assertEqual(self.lines("libs", "ventas/worker"), ["_shared/libs/goauth", "_shared/libs/golog"])

    def test_deps_txt(self) -> None:
        self.libs(["auth"])
        self.write({"infra/red/main.tf": "", "infra/red/deps.txt": "# módulos\n./_shared/libs/auth/  # auth\n\n"})
        self.assertEqual(self.lines("paths", "infra/red"), ["_shared/libs/auth", "infra/red"])

    def test_transitive_and_cycle(self) -> None:
        self.write({
            "_shared/libs/a/deps.txt": "_shared/libs/b\n",
            "_shared/libs/b/deps.txt": "_shared/libs/a\n_shared/libs/c\n",
            "_shared/libs/c/x.txt": "",
            "svc/deps.txt": "_shared/libs/a\n",
        })
        self.assertEqual(self.lines("libs", "svc"), ["_shared/libs/a", "_shared/libs/b", "_shared/libs/c"])

    def test_several_dirs_union(self) -> None:
        self.libs(["auth", "logging"])
        self.write({"a/deps.txt": "_shared/libs/auth\n", "b/deps.txt": "_shared/libs/logging\n"})
        self.assertEqual(self.lines("paths", "a", "b/"),
                         ["_shared/libs/auth", "_shared/libs/logging", "a", "b"])

    def test_no_manifest_means_no_deps(self) -> None:
        self.write({"solo/Dockerfile": "FROM busybox\n"})
        self.assertEqual(self.lines("paths", "solo"), ["solo"])


class TestConsumers(DepsTestCase):
    def test_direct_and_transitive(self) -> None:
        self.write({
            "_shared/libs/auth/deps.txt": "_shared/libs/crypto\n",
            "_shared/libs/crypto/x.txt": "",
            "ventas/api/deps.txt": "_shared/libs/auth\n",
            "compras/api/deps.txt": "_shared/libs/crypto\n",
            "rh/api/deps.txt": "\n",
        })
        self.assertEqual(self.lines("consumers", "_shared/libs/crypto"),
                         ["_shared/libs/auth", "compras/api", "ventas/api"])
        self.assertEqual(self.lines("consumers", "_shared/libs/auth"), ["ventas/api"])

    def test_broken_manifest_elsewhere_is_skipped_with_warning(self) -> None:
        self.libs(["auth"])
        self.write({"ventas/api/deps.txt": "_shared/libs/auth\n", "roto/pyproject.toml": "[project\n"})
        r = self.run_deps("consumers", "_shared/libs/auth")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["ventas/api"])
        self.assertIn("roto se omitió", r.stderr)


class TestGitSource(DepsTestCase):
    def test_ref_reads_old_tag(self) -> None:
        self.libs(["auth", "logging"])
        self.write({"svc/deps.txt": "_shared/libs/auth\n"})
        _git(self.repo, "tag", "svc@1.0.0")
        self.write({"svc/deps.txt": "_shared/libs/auth\n_shared/libs/logging\n"})
        self.assertEqual(self.lines("libs", "svc", "--ref", "svc@1.0.0"), ["_shared/libs/auth"])
        self.assertEqual(self.lines("libs", "svc"), ["_shared/libs/auth", "_shared/libs/logging"])

    def test_works_outside_sparse_checkout(self) -> None:
        self.libs(["auth"])
        self.write({"ventas/api/deps.txt": "_shared/libs/auth\n", "otro/x.txt": ""})
        _git(self.repo, "sparse-checkout", "set", "--cone", "otro")
        self.assertFalse((self.repo / "ventas" / "api").exists())
        self.assertEqual(self.lines("paths", "ventas/api"), ["_shared/libs/auth", "ventas/api"])


class TestErrors(DepsTestCase):
    def test_missing_dependency_dir(self) -> None:
        self.write({"svc/deps.txt": "_shared/libs/nope\n"})
        r = self.run_deps("paths", "svc")
        self.assertEqual(r.returncode, 1)
        self.assertIn("svc/deps.txt: la carpeta _shared/libs/nope no existe", r.stderr)

    def test_missing_argument_dir(self) -> None:
        r = self.run_deps("paths", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nope no existe", r.stderr)

    def test_path_outside_repo(self) -> None:
        self.write({"svc/pyproject.toml": '[tool.uv.sources]\nx = { path = "../../../fuera" }\n'})
        r = self.run_deps("paths", "svc")
        self.assertEqual(r.returncode, 1)
        self.assertIn("sale del repo", r.stderr)

    def test_unknown_workspace_package(self) -> None:
        self.write({"svc/pyproject.toml": '[tool.uv.sources]\nmetalsa-nope = { workspace = true }\n'})
        r = self.run_deps("paths", "svc")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no hay ningún paquete 'metalsa-nope'", r.stderr)

    def test_usage(self) -> None:
        self.assertEqual(self.run_deps().returncode, 2)
        self.assertEqual(self.run_deps("nope").returncode, 2)

    def test_old_python_message(self) -> None:
        code = ("import runpy, sys; sys.modules['tomllib'] = None; "
                f"sys.argv = ['deps.py', 'paths', 'x']; runpy.run_path({str(SCRIPT)!r}, run_name='__main__')")
        r = subprocess.run([sys.executable, "-c", code], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("requiere Python >= 3.11", r.stderr)


class TestOutputs(DepsTestCase):
    def test_trigger_block_and_policy_filter(self) -> None:
        self.libs(["auth"])
        self.write({"ventas/api/deps.txt": "_shared/libs/auth\n"})
        self.assertEqual(self.lines("trigger-block", "ventas/api"),
                         ["  paths:", "    include:", "      - _shared/libs/auth", "      - ventas/api"])
        self.assertEqual(self.lines("policy-filter", "ventas/api"), ["/_shared/libs/auth/*; /ventas/api/*"])


if __name__ == "__main__":
    unittest.main()
