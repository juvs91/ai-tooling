"""
test_check_skill_frontmatter.py — Suite de tests para
tools/check_skill_frontmatter.py.

Corre con stdlib unittest (sin dependencia de pytest, consistente con el
mandato de "sin dependencias externas pesadas" del propio checker):

  python3 -m unittest tools/tests/test_check_skill_frontmatter.py -v

Cobertura:
  - happy path: SKILL.md válido con frontmatter y `name` no vacío.
  - edge cases: sin frontmatter (no empieza con '---'), frontmatter sin
    cerrar, frontmatter sin campo `name`, frontmatter con `name` vacío
    (sin comillas, con comillas simples, con comillas dobles), `name`
    indentado (no debe contar como nivel superior), múltiples archivos
    mixtos (válidos + inválidos) en el mismo árbol.
  - contrato de CLI: exit code 0 cuando todo es válido, 1 cuando hay al
    menos un problema, 2 cuando --root no existe; y que --json emita un
    JSON parseable con las claves documentadas.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_skill_frontmatter as csf  # noqa: E402  (después del sys.path.insert)

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "check_skill_frontmatter.py"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class CheckFileUnitTests(unittest.TestCase):
    """Tests unitarios directos sobre check_file(), sin pasar por subprocess."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_happy_path_valid_frontmatter(self) -> None:
        p = self.root / "SKILL.md"
        _write(p, "---\nname: my-skill\ndescription: something\n---\n# Body\n")
        self.assertIsNone(csf.check_file(p, "name"))

    def test_no_frontmatter_at_all(self) -> None:
        p = self.root / "SKILL.md"
        _write(p, "# Just a heading\nNo frontmatter here.\n")
        finding = csf.check_file(p, "name")
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.reason, csf.REASON_NO_FRONTMATTER)

    def test_unterminated_frontmatter_counts_as_no_frontmatter(self) -> None:
        p = self.root / "SKILL.md"
        _write(p, "---\nname: my-skill\n# never closed\n")
        finding = csf.check_file(p, "name")
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.reason, csf.REASON_NO_FRONTMATTER)

    def test_frontmatter_without_name_field(self) -> None:
        p = self.root / "SKILL.md"
        _write(p, "---\ndescription: no name here\n---\n# Body\n")
        finding = csf.check_file(p, "name")
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.reason, csf._reason_no_field("name"))

    def test_frontmatter_with_empty_name_no_quotes(self) -> None:
        p = self.root / "SKILL.md"
        _write(p, "---\nname:\ndescription: x\n---\n# Body\n")
        finding = csf.check_file(p, "name")
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.reason, csf._reason_empty_field("name"))

    def test_frontmatter_with_empty_name_double_quotes(self) -> None:
        p = self.root / "SKILL.md"
        _write(p, '---\nname: ""\n---\n# Body\n')
        finding = csf.check_file(p, "name")
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.reason, csf._reason_empty_field("name"))

    def test_frontmatter_with_empty_name_single_quotes(self) -> None:
        p = self.root / "SKILL.md"
        _write(p, "---\nname: ''\n---\n# Body\n")
        finding = csf.check_file(p, "name")
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.reason, csf._reason_empty_field("name"))

    def test_name_with_quotes_is_valid(self) -> None:
        p = self.root / "SKILL.md"
        _write(p, '---\nname: "my-skill"\n---\n# Body\n')
        self.assertIsNone(csf.check_file(p, "name"))

    def test_indented_name_does_not_count_as_top_level(self) -> None:
        # Un `name:` indentado bajo otra clave no debe satisfacer el
        # requisito de nivel superior.
        p = self.root / "SKILL.md"
        _write(p, "---\nallowed-tools:\n  name: nested-not-top-level\n---\n# Body\n")
        finding = csf.check_file(p, "name")
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.reason, csf._reason_no_field("name"))

    def test_custom_field_name(self) -> None:
        p = self.root / "AGENT.md"
        _write(p, "---\nid: some-id\n---\n# Body\n")
        self.assertIsNone(csf.check_file(p, "id"))
        finding = csf.check_file(p, "name")
        self.assertIsNotNone(finding)


class RunCheckTests(unittest.TestCase):
    """Tests sobre run_check() recorriendo un árbol con archivos mixtos."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_zero_files_found_is_not_an_error(self) -> None:
        findings, total = csf.run_check(self.root, "SKILL.md", "name")
        self.assertEqual(findings, [])
        self.assertEqual(total, 0)

    def test_mixed_tree_reports_only_problematic_files(self) -> None:
        _write(self.root / "a" / "SKILL.md", "---\nname: skill-a\n---\n")
        _write(self.root / "b" / "SKILL.md", "# no frontmatter\n")
        _write(self.root / "c" / "deep" / "SKILL.md", "---\ndescription: x\n---\n")
        findings, total = csf.run_check(self.root, "SKILL.md", "name")
        self.assertEqual(total, 3)
        problem_paths = {Path(f.path).name for f in findings}
        self.assertEqual(len(findings), 2)
        # Confirma que el válido (a/SKILL.md) NO aparece entre los findings.
        self.assertNotIn(str(self.root / "a" / "SKILL.md"), [f.path for f in findings])
        self.assertTrue(problem_paths.issubset({"SKILL.md"}))


class CliContractTests(unittest.TestCase):
    """Tests end-to-end vía subprocess — validan el contrato de CLI real
    (exit codes, --json, --root inexistente) tal como lo vería un agente o
    un hook de pre-commit invocando el script."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            capture_output=True,
            text=True,
        )

    def test_exit_0_when_all_valid(self) -> None:
        skills_dir = self.root / "skills"
        _write(skills_dir / "ok" / "SKILL.md", "---\nname: ok-skill\n---\n")
        result = self._run("--root", str(skills_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_exit_1_when_one_invalid(self) -> None:
        skills_dir = self.root / "skills"
        _write(skills_dir / "ok" / "SKILL.md", "---\nname: ok-skill\n---\n")
        _write(skills_dir / "broken" / "SKILL.md", "# no frontmatter\n")
        result = self._run("--root", str(skills_dir))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("broken", result.stdout)
        self.assertIn(csf.REASON_NO_FRONTMATTER, result.stdout)

    def test_exit_2_when_root_missing(self) -> None:
        result = self._run("--root", str(self.root / "does-not-exist"))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("does-not-exist", result.stderr)

    def test_json_output_is_parseable_and_has_expected_keys(self) -> None:
        skills_dir = self.root / "skills"
        _write(skills_dir / "ok" / "SKILL.md", "---\nname: ok-skill\n---\n")
        _write(skills_dir / "broken" / "SKILL.md", "---\nname: ''\n---\n")
        result = self._run("--root", str(skills_dir), "--json")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["total_files"], 2)
        self.assertEqual(payload["problem_count"], 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(len(payload["problems"]), 1)
        self.assertIn("reason", payload["problems"][0])
        self.assertIn("path", payload["problems"][0])

    def test_custom_field_and_filename_are_respected(self) -> None:
        agents_dir = self.root / "agents"
        _write(agents_dir / "x" / "AGENT.md", "---\nid: agent-x\n---\n")
        result = self._run(
            "--root", str(agents_dir), "--filename", "AGENT.md", "--field", "id"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
