"""
test_check_adr_sections.py — Suite de tests para tools/check_adr_sections.py.

Corre con stdlib unittest (sin dependencias externas):

  python3 -m unittest tools/tests/test_check_adr_sections.py -v

Cobertura:
  - happy path: ADR con las 4 secciones en heading-style y en
    inline-field-style (con y sin viñeta, con y sin negrita alrededor).
  - edge cases: falta una sección, faltan varias, heading con texto extra
    después del nombre de sección (ej. "## Context and Problem Statement",
    "## Decision Outcome"), sección en negrita con el ":" DENTRO de la
    negrita (`**Status:**`) vs. fuera (`**Status**:`).
  - i18n: `--i18n-es` acepta Estado/Fecha/Contexto/Decisión; sin el flag,
    un ADR solo-en-español se reporta incompleto.
  - contrato de CLI: exit codes 0/1/2, --json, --sections custom.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_adr_sections as cas  # noqa: E402  (después del sys.path.insert)

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "check_adr_sections.py"

DEFAULT_SECTIONS = ("Status", "Date", "Context", "Decision")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class CheckFileUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_happy_path_heading_style(self) -> None:
        p = self.root / "ADR-0001-x.md"
        _write(
            p,
            "# ADR-0001: X\n\n"
            "## Status\nAccepted\n\n"
            "## Date\n2026-01-01\n\n"
            "## Context\nblah\n\n"
            "## Decision\nblah\n",
        )
        self.assertIsNone(cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False))

    def test_happy_path_bulleted_bold_field_style(self) -> None:
        p = self.root / "ADR-0002-x.md"
        _write(
            p,
            "# ADR-0002: X\n\n"
            "- **Date**: 2026-03-22\n"
            "- **Status**: Accepted\n\n"
            "## Context\nblah\n\n"
            "## Decision\nblah\n",
        )
        self.assertIsNone(cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False))

    def test_happy_path_bold_colon_inside_field_style(self) -> None:
        # "**Status:**" (colon DENTRO de la negrita) — variante real
        # encontrada en varios ADR de este repo, distinta de "**Status**:".
        p = self.root / "ADR-0004-x.md"
        _write(
            p,
            "# ADR-0004: X\n\n"
            "**Status:** Accepted\n"
            "**Date:** 2026-01-01\n\n"
            "## Context\nblah\n\n"
            "## Decision\nblah\n",
        )
        self.assertIsNone(cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False))

    def test_inline_field_style_without_heading(self) -> None:
        # Context/Decision también pueden aparecer como field inline
        # (ej. ADR-0010 real: "**Context**: Kimi K2 proxy...").
        p = self.root / "ADR-0010-x.md"
        _write(
            p,
            "# ADR-0010: X\n\n"
            "**Status**: Accepted  \n"
            "**Date**: 2026-06-30  \n"
            "**Context**: algo pasa\n\n"
            "## Problem\nblah\n\n"
            "## Decision\nblah\n",
        )
        self.assertIsNone(cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False))

    def test_heading_with_trailing_words_counts(self) -> None:
        # "## Context and Problem Statement" / "## Decision Outcome" deben
        # contar como Context / Decision (prefix match con límite de palabra).
        p = self.root / "ADR-0005-x.md"
        _write(
            p,
            "# ADR-0005: X\n\n"
            "## Status\nAccepted\n\n"
            "## Date\n2026-01-01\n\n"
            "## Context and Problem Statement\nblah\n\n"
            "## Decision Outcome\nblah\n",
        )
        self.assertIsNone(cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False))

    def test_missing_one_section(self) -> None:
        p = self.root / "ADR-0006-x.md"
        _write(
            p,
            "# ADR-0006: X\n\n"
            "- **Date**: 2026-01-01\n"
            "- **Status**: Accepted\n\n"
            "## Context\nblah\n",
        )
        finding = cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False)
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.missing_sections, ("Decision",))

    def test_missing_all_sections(self) -> None:
        p = self.root / "ADR-0007-x.md"
        _write(p, "# ADR-0007: X\n\nJust prose, no metadata at all.\n")
        finding = cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False)
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.missing_sections, DEFAULT_SECTIONS)

    def test_unreadable_file_reports_all_missing(self) -> None:
        p = self.root / "does-not-exist.md"
        finding = cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False)
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.missing_sections, DEFAULT_SECTIONS)

    def test_spanish_only_fails_without_i18n_flag(self) -> None:
        p = self.root / "ADR-0017-x.md"
        _write(
            p,
            "# ADR-0017: X\n\n"
            "**Estado:** Accepted\n"
            "**Fecha:** 2026-07-13\n\n"
            "## Contexto\nblah\n\n"
            "## Decisión\nblah\n",
        )
        finding = cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False)
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(set(finding.missing_sections), set(DEFAULT_SECTIONS))

    def test_spanish_passes_with_i18n_flag(self) -> None:
        p = self.root / "ADR-0017-x.md"
        _write(
            p,
            "# ADR-0017: X\n\n"
            "**Estado:** Accepted\n"
            "**Fecha:** 2026-07-13\n\n"
            "## Contexto\nblah\n\n"
            "## Decisión\nblah\n",
        )
        self.assertIsNone(cas.check_file(p, DEFAULT_SECTIONS, i18n_es=True))

    def test_status_heading_with_value_on_next_line(self) -> None:
        # Variante real ADR-0022: "## Status\nAccepted — 2026-07-15" (Date
        # embebido en el contenido, no como campo propio — Date debe seguir
        # faltando si no aparece en ninguna forma separada).
        p = self.root / "ADR-0022-x.md"
        _write(
            p,
            "# ADR-0022: X\n\n"
            "## Status\nAccepted — 2026-07-15\n\n"
            "## Context\nblah\n\n"
            "## Decision\nblah\n",
        )
        finding = cas.check_file(p, DEFAULT_SECTIONS, i18n_es=False)
        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.missing_sections, ("Date",))


class RunCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_zero_files_found_is_not_an_error(self) -> None:
        findings, total = cas.run_check(
            self.root, "ADR-*.md", DEFAULT_SECTIONS, i18n_es=False
        )
        self.assertEqual(findings, [])
        self.assertEqual(total, 0)

    def test_mixed_tree_reports_only_problematic_files(self) -> None:
        _write(
            self.root / "ADR-0001-ok.md",
            "## Status\nx\n## Date\nx\n## Context\nx\n## Decision\nx\n",
        )
        _write(self.root / "ADR-0002-broken.md", "## Status\nx\n## Date\nx\n")
        findings, total = cas.run_check(
            self.root, "ADR-*.md", DEFAULT_SECTIONS, i18n_es=False
        )
        self.assertEqual(total, 2)
        self.assertEqual(len(findings), 1)
        self.assertIn("ADR-0002-broken.md", findings[0].path)

    def test_pattern_is_respected_non_recursive(self) -> None:
        _write(self.root / "ADR-0001-ok.md", "## Status\nx\n")
        _write(self.root / "sub" / "ADR-0002-nested.md", "## Status\nx\n")
        findings, total = cas.run_check(
            self.root, "ADR-*.md", ("Status",), i18n_es=False
        )
        self.assertEqual(total, 1)  # no recursion — el anidado no cuenta


class CliContractTests(unittest.TestCase):
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
        adr_dir = self.root / "adr"
        _write(
            adr_dir / "ADR-0001-ok.md",
            "## Status\nx\n## Date\nx\n## Context\nx\n## Decision\nx\n",
        )
        result = self._run("--root", str(adr_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_exit_1_when_one_invalid(self) -> None:
        adr_dir = self.root / "adr"
        _write(
            adr_dir / "ADR-0001-ok.md",
            "## Status\nx\n## Date\nx\n## Context\nx\n## Decision\nx\n",
        )
        _write(adr_dir / "ADR-0002-broken.md", "## Status\nx\n")
        result = self._run("--root", str(adr_dir))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ADR-0002-broken.md", result.stdout)
        self.assertIn("Date", result.stdout)

    def test_exit_2_when_root_missing(self) -> None:
        result = self._run("--root", str(self.root / "does-not-exist"))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("does-not-exist", result.stderr)

    def test_json_output_is_parseable_and_has_expected_keys(self) -> None:
        adr_dir = self.root / "adr"
        _write(
            adr_dir / "ADR-0001-ok.md",
            "## Status\nx\n## Date\nx\n## Context\nx\n## Decision\nx\n",
        )
        _write(adr_dir / "ADR-0002-broken.md", "## Status\nx\n")
        result = self._run("--root", str(adr_dir), "--json")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["total_files"], 2)
        self.assertEqual(payload["problem_count"], 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(len(payload["problems"]), 1)
        self.assertIn("missing_sections", payload["problems"][0])
        self.assertIn("path", payload["problems"][0])

    def test_custom_sections_are_respected(self) -> None:
        adr_dir = self.root / "adr"
        _write(adr_dir / "ADR-0001-x.md", "## Consequences\nx\n")
        result = self._run("--root", str(adr_dir), "--sections", "Consequences")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_i18n_flag_via_cli(self) -> None:
        adr_dir = self.root / "adr"
        _write(
            adr_dir / "ADR-0017-x.md",
            "**Estado:** x\n**Fecha:** x\n## Contexto\nx\n## Decisión\nx\n",
        )
        result_strict = self._run("--root", str(adr_dir))
        self.assertEqual(result_strict.returncode, 1)
        result_i18n = self._run("--root", str(adr_dir), "--i18n-es")
        self.assertEqual(result_i18n.returncode, 0, result_i18n.stdout + result_i18n.stderr)


if __name__ == "__main__":
    unittest.main()
