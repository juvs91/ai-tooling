"""
test_ci_pylib.py — Suite de tests para scripts/ci-pylib.sh, scripts/ci-python.sh y
scripts/ci_pyproject.py.

Corre con stdlib unittest:

  python3 -m unittest discover -s tools/tests -p test_ci_pylib.py -v

Git real; `python -m pip`, `python -m twine` y `uv` son fakes que registran la
llamada (cualquier otro `python …` delega en el python3 real, así ci_pyproject.py
corre de verdad). La promoción a views usa curl real contra un HTTP server local.

Cobertura (ADR-0060 §10 / ADR-0061):
  - ci_pyproject: set-version reescribe solo la primera `version =`; name; errores.
  - validate: tag dev/rc/prod → pep440; manifest ≠ versión del tag → error.
  - build: reescribe la versión del pyproject del agente y llama uv build en la lib.
  - upload: OK; 409/already exists → warning y exit 0; otro error → exit 1.
  - promote-view: dev no promueve; rc → Prerelease, prod → Release con la URL
    de feed de proyecto y de organización; HTTP 4xx → error.
  - ci-python: check-paths, install-uv con/sin versión, lint y test corren en
    CI_WORKING_DIRECTORY.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ci_image import _Repo  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
VSO = re.compile(r"^##vso\[task\.setvariable variable=(\w+);isOutput=true\](.*)$", re.M)

FAKE_PYTHON = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    printf 'python %s\\n' "$*" >> "$FAKE_LOG"
    if [ "$1" = -m ] && [ "$2" = pip ]; then exit 0; fi
    if [ "$1" = -m ] && [ "$2" = twine ]; then
      case "${FAKE_TWINE:-ok}" in
        ok)  echo "Uploading ... 200 OK"; exit 0 ;;
        409) echo "HTTPError: 409 Conflict from https://pkgs/upload: file already exists"; exit 1 ;;
        *)   echo "HTTPError: 500 Internal Server Error"; exit 1 ;;
      esac
    fi
    exec python3 "$@"
    """
)
FAKE_UV = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    printf 'uv %s @ %s\\n' "$*" "$PWD" >> "$FAKE_LOG"
    if [ "$1" = build ]; then mkdir -p dist && touch dist/pkg-0.0.0-py3-none-any.whl; fi
    exit 0
    """
)


class _Feed(BaseHTTPRequestHandler):
    calls: list[dict] = []
    status = 200

    def do_PATCH(self) -> None:  # noqa: N802 — nombre fijado por BaseHTTPRequestHandler
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        _Feed.calls.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": json.loads(body)})
        self.send_response(_Feed.status)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args) -> None:  # silenciar
        pass


class PylibTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _Feed)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def setUp(self) -> None:
        _Feed.calls, _Feed.status = [], 200
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        (tmp / "repo").mkdir()
        self.repo = _Repo(tmp / "repo")
        self.repo.write("_shared/libs/auth/pyproject.toml",
                        '[project]\nname = "metalsa-auth"\nversion = "0.2.0"\n\n[tool.x]\nversion = "keep"\n')
        self.repo.write("ventas/api/pyproject.toml", '[project]\nname = "ventas-api"\nversion = "1.0.0"\n')
        self.repo.write("pipelines/api-ci.yml", "trigger:\n  paths:\n    include:\n      - ventas/api\n      - _shared/libs/auth\n")
        self.repo.commit("init")
        self.log = tmp / "calls.log"
        self.log.write_text("")
        self.agent_tmp = tmp / "agent-tmp"
        self.agent_tmp.mkdir()
        bindir = tmp / "bin"
        bindir.mkdir()
        for name, body in (("python", FAKE_PYTHON), ("uv", FAKE_UV)):
            (bindir / name).write_text(body)
            (bindir / name).chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_LOG": str(self.log),
                    "LC_ALL": "C", "AGENT_TEMPDIRECTORY": str(self.agent_tmp),
                    "CI_LIB_NAME": "auth", "CI_LIB_PATH": "_shared/libs/auth",
                    "CI_ARTIFACT_FEED": "PRO_ALL_INNO/python-libs",
                    "CI_VIEW_RC": "Prerelease", "CI_VIEW_PROD": "Release",
                    "SYSTEM_ACCESSTOKEN": "tok", "SYSTEM_COLLECTIONURI": "https://dev.azure.com/metalsa/",
                    "CI_PKGS_BASE_URL": self.base_url, "PYPIRC_PATH": str(tmp / ".pypirc")}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_script(self, script: str, cmd: str, **extra: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        r = subprocess.run(["bash", str(SCRIPTS / script), cmd], cwd=self.repo.root,
                           capture_output=True, text=True, env={**self.env, **extra})
        return r, dict(VSO.findall(r.stdout))

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()


class TestPyproject(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.file = Path(self._tmp.name) / "pyproject.toml"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_py(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPTS / "ci_pyproject.py"), *args], capture_output=True, text=True)

    def test_set_version_rewrites_only_first(self) -> None:
        self.file.write_text('[project]\nname = "a"\nversion = "1.0.0"\n[tool.x]\nversion = "keep"\n')
        self.assertEqual(self.run_py("set-version", str(self.file), "1.0.0rc2").returncode, 0)
        text = self.file.read_text()
        self.assertIn('version = "1.0.0rc2"', text)
        self.assertIn('version = "keep"', text)

    def test_set_version_without_version_fails(self) -> None:
        self.file.write_text('[project]\nname = "a"\n')
        r = self.run_py("set-version", str(self.file), "1.0.0")
        self.assertEqual(r.returncode, 1)

    def test_name(self) -> None:
        self.file.write_text('[project]\nname = "metalsa-auth"\nversion = "1"\n')
        r = self.run_py("name", str(self.file))
        self.assertEqual(r.stdout.strip(), "metalsa-auth")

    def test_usage(self) -> None:
        self.assertEqual(self.run_py("nope").returncode, 2)


class TestValidate(PylibTestCase):
    def test_pep440_per_env(self) -> None:
        for tag, pep, env in (("auth@0.2.0-dev.7", "0.2.0.dev7", "dev"),
                              ("auth@0.2.0-rc.3", "0.2.0rc3", "qa"),
                              ("auth@0.2.0", "0.2.0", "prod")):
            with self.subTest(tag=tag):
                r, out = self.run_script("ci-pylib.sh", "validate", BUILD_SOURCEBRANCH=f"refs/tags/{tag}")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(out["pep440"], pep)
                self.assertEqual(out["env"], env)

    def test_manifest_mismatch(self) -> None:
        r, _ = self.run_script("ci-pylib.sh", "validate", BUILD_SOURCEBRANCH="refs/tags/auth@0.3.0-rc.1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("##vso[task.logissue type=error]", r.stdout)

    def test_hotfix_rejected(self) -> None:
        r, _ = self.run_script("ci-pylib.sh", "validate", BUILD_SOURCEBRANCH="refs/tags/auth@0.2.0-hotfix.1")
        self.assertNotEqual(r.returncode, 0)


class TestBuildUpload(PylibTestCase):
    def test_build_rewrites_version_and_builds_in_lib(self) -> None:
        r, _ = self.run_script("ci-pylib.sh", "build", CI_PEP440="0.2.0rc3")
        self.assertEqual(r.returncode, 0, r.stderr)
        pyproject = (self.repo.root / "_shared/libs/auth/pyproject.toml").read_text()
        self.assertIn('version = "0.2.0rc3"', pyproject)
        self.assertTrue(any(c.startswith("uv build --out-dir dist @ ") and c.endswith("_shared/libs/auth")
                            for c in self.calls()), self.calls())

    def test_upload_ok(self) -> None:
        r, _ = self.run_script("ci-pylib.sh", "upload", CI_PEP440="0.2.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any("twine upload -r python-libs" in c for c in self.calls()))

    def test_upload_409_is_rerun(self) -> None:
        r, _ = self.run_script("ci-pylib.sh", "upload", CI_PEP440="0.2.0", FAKE_TWINE="409")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("type=warning", r.stdout)

    def test_upload_other_error_fails(self) -> None:
        r, _ = self.run_script("ci-pylib.sh", "upload", CI_PEP440="0.2.0", FAKE_TWINE="500")
        self.assertEqual(r.returncode, 1)


class TestPromoteView(PylibTestCase):
    def test_dev_stays_local(self) -> None:
        r, _ = self.run_script("ci-pylib.sh", "promote-view", CI_PEP440="0.2.0.dev7", CI_TAG_ENV="dev")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_Feed.calls, [])

    def test_rc_and_prod_views_with_project_feed(self) -> None:
        for env, pep, view in (("qa", "0.2.0rc3", "Prerelease"), ("prod", "0.2.0", "Release")):
            with self.subTest(env=env):
                _Feed.calls = []
                r, _ = self.run_script("ci-pylib.sh", "promote-view", CI_PEP440=pep, CI_TAG_ENV=env)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                call = _Feed.calls[0]
                self.assertEqual(call["path"],
                                 f"/metalsa/PRO_ALL_INNO/_apis/packaging/feeds/python-libs/pypi/packages/"
                                 f"metalsa-auth/versions/{pep}?api-version=7.1-preview.1")
                self.assertEqual(call["auth"], "Bearer tok")
                self.assertEqual(call["body"]["views"]["value"], view)

    def test_org_feed_url(self) -> None:
        r, _ = self.run_script("ci-pylib.sh", "promote-view", CI_PEP440="0.2.0", CI_TAG_ENV="prod",
                               CI_ARTIFACT_FEED="python-libs")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(_Feed.calls[0]["path"].startswith("/metalsa/_apis/packaging/feeds/python-libs/"))

    def test_http_error_fails(self) -> None:
        _Feed.status = 404
        r, _ = self.run_script("ci-pylib.sh", "promote-view", CI_PEP440="0.2.0", CI_TAG_ENV="prod")
        self.assertEqual(r.returncode, 1)
        self.assertIn("HTTP 404", r.stdout)


class TestCiPython(PylibTestCase):
    def test_check_paths(self) -> None:
        ok, _ = self.run_script("ci-python.sh", "check-paths", CI_PIPELINE_FILE="pipelines/api-ci.yml",
                                CI_WORKING_DIRECTORY="ventas/api", CI_DEPENDS_ON="_shared/libs/auth")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad, _ = self.run_script("ci-python.sh", "check-paths", CI_PIPELINE_FILE="pipelines/api-ci.yml",
                                 CI_WORKING_DIRECTORY="ventas/api", CI_DEPENDS_ON="_shared/libs/otra")
        self.assertEqual(bad.returncode, 1)

    def test_install_uv_pins_version(self) -> None:
        self.run_script("ci-python.sh", "install-uv", CI_UV_VERSION="0.5.1")
        self.run_script("ci-python.sh", "install-uv", CI_UV_VERSION="")
        pips = [c for c in self.calls() if c.startswith("python -m pip")]
        self.assertTrue(pips[0].endswith("uv==0.5.1"))
        self.assertTrue(pips[1].endswith(" uv"))

    def test_lint_and_test_run_in_working_directory(self) -> None:
        for cmd in ("lint", "test"):
            r, _ = self.run_script("ci-python.sh", cmd, CI_WORKING_DIRECTORY="ventas/api")
            self.assertEqual(r.returncode, 0, r.stderr)
        uv = [c for c in self.calls() if c.startswith("uv ")]
        self.assertTrue(all(c.endswith("ventas/api") for c in uv), uv)
        self.assertIn("ruff format --check", " ".join(uv))
        self.assertTrue(any(f"--junitxml={self.agent_tmp}/test-results.xml" in c for c in uv))


if __name__ == "__main__":
    unittest.main()
