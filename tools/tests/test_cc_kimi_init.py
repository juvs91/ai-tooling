"""
test_cc_kimi_init.py — Suite de tests para tools/cc_kimi_init.py.

Corre con stdlib unittest (sin dependencias externas):

  python3 -m unittest tools/tests/test_cc_kimi_init.py -v

Cobertura:
  - happy path: crea settings.local.json desde el template, gitignore
    entries, trust en claude.json, key store central con --key-file.
  - idempotencia: re-correr no duplica entradas en .gitignore y no
    sobrescribe settings.local.json existente (sin --force).
  - --force sobrescribe el settings.local.json existente.
  - --dry-run no escribe nada.
  - --no-trust no toca claude.json.
  - contrato de CLI: JSON estricto en stdout, exit 0; exit 2 con target
    inexistente; apiKeyHelper queda expandido a ruta absoluta.

Aíslamiento: CC_KIMI_CONFIG_DIR y CC_CLAUDE_JSON apuntan a tmp en cada
test — nunca se toca ~/.config/cc-kimi ni ~/.claude.json reales.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cc_kimi_init  # noqa: E402


class CcKimiInitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.target = tmp / "proyecto"
        self.target.mkdir()
        self.config_dir = tmp / "cc-kimi"
        self.claude_json = tmp / "claude.json"
        self.claude_json.write_text(json.dumps({"projects": {}}))
        self.key_file = tmp / "mi-key.txt"
        self.key_file.write_text("sk-kimi-TESTKEY\n")
        os.environ["CC_KIMI_CONFIG_DIR"] = str(self.config_dir)
        os.environ["CC_CLAUDE_JSON"] = str(self.claude_json)
        self.addCleanup(os.environ.pop, "CC_KIMI_CONFIG_DIR", None)
        self.addCleanup(os.environ.pop, "CC_CLAUDE_JSON", None)

    def _run(self, *argv: str) -> tuple[int, dict]:
        from io import StringIO
        import contextlib

        stdout, stderr = StringIO(), StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cc_kimi_init.main(list(argv))
        # Éxito: JSON en stdout. Error (exit 2): JSON en stderr, stdout vacío.
        raw = stdout.getvalue() or stderr.getvalue()
        return code, json.loads(raw)

    def test_happy_path(self) -> None:
        code, out = self._run(str(self.target), "--key-file", str(self.key_file))
        self.assertEqual(code, 0)
        settings = self.target / ".claude" / "settings.local.json"
        self.assertTrue(settings.is_file())
        data = json.loads(settings.read_text())
        self.assertEqual(data["model"], "kimi-for-coding")
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "https://api.kimi.com/coding")
        self.assertNotIn("ANTHROPIC_API_KEY", data["env"],
                         "la key NO va en el env del proyecto — gana sobre apiKeyHelper y rompe requests")
        self.assertFalse(data["apiKeyHelper"].startswith("~"), "apiKeyHelper debe quedar absoluto")
        self.assertNotIn("/..", data["apiKeyHelper"], "apiKeyHelper no debe tener path roto con doble punto")
        # key store central
        key = self.config_dir / "api-key"
        self.assertEqual(key.read_text().strip(), "sk-kimi-TESTKEY")
        self.assertTrue(os.access(key, os.X_OK) is False)  # 0600: no ejecutable
        self.assertTrue((self.config_dir / "key-helper.sh").is_file())
        # gitignore
        gitignore = (self.target / ".gitignore").read_text()
        self.assertIn(".claude/settings.local.json", gitignore)
        # trust
        claude = json.loads(self.claude_json.read_text())
        self.assertTrue(claude["projects"][str(self.target.resolve())]["hasTrustDialogAccepted"])

    def test_idempotent_gitignore_and_settings(self) -> None:
        self._run(str(self.target), "--key-file", str(self.key_file))
        code, out = self._run(str(self.target))
        self.assertEqual(code, 0)
        self.assertFalse(out["settings"]["written"])
        self.assertIn("exists", out["settings"]["reason"])
        gitignore = (self.target / ".gitignore").read_text()
        self.assertEqual(gitignore.count(".claude/settings.local.json"), 1)

    def test_force_overwrites_settings(self) -> None:
        self._run(str(self.target), "--key-file", str(self.key_file))
        settings = self.target / ".claude" / "settings.local.json"
        settings.write_text('{"model": "custom"}')
        code, out = self._run(str(self.target), "--force")
        self.assertEqual(code, 0)
        self.assertTrue(out["settings"]["written"])
        self.assertEqual(json.loads(settings.read_text())["model"], "kimi-for-coding")

    def test_dry_run_writes_nothing(self) -> None:
        code, out = self._run(str(self.target), "--key-file", str(self.key_file), "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(out["dry_run"])
        self.assertFalse((self.target / ".claude" / "settings.local.json").exists())
        self.assertFalse((self.target / ".gitignore").exists())
        self.assertFalse((self.config_dir / "api-key").exists())

    def test_no_trust_skips_claude_json(self) -> None:
        before = self.claude_json.read_text()
        code, _ = self._run(str(self.target), "--no-trust")
        self.assertEqual(code, 0)
        self.assertEqual(self.claude_json.read_text(), before)

    def test_missing_target_exit_2(self) -> None:
        code, _ = self._run(str(self.target / "no-existe"))
        self.assertEqual(code, 2)

    def test_stdout_is_strict_json(self) -> None:
        from io import StringIO
        import contextlib

        stdout = StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(StringIO()):
            cc_kimi_init.main([str(self.target), "--key-file", str(self.key_file)])
        payload = json.loads(stdout.getvalue())  # raises if not strict JSON
        self.assertEqual(payload["tool"], "cc-kimi-init")


if __name__ == "__main__":
    unittest.main()
