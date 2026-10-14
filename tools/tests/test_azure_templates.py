"""
test_azure_templates.py — Validación estática de templates/gitops/azure-pipelines/.

Corre con stdlib unittest + PyYAML:

  python3 -m unittest tools/tests/test_azure_templates.py -v

No reemplaza una corrida real en Azure DevOps (ADR-0060 lista lo que solo un
pipeline sandbox puede validar), pero atrapa los errores baratos:
  - todo YAML parsea;
  - cada `template:` apunta a un archivo existente (ruta relativa al que lo usa);
  - cada `${{ parameters.X }}` usado en un template está declarado;
  - cada uso de template pasa los parámetros sin default y no pasa desconocidos;
  - cada script `bash:` / `inlineScript:` pasa `bash -n` (expresiones de Azure
    reemplazadas por valores dummy);
  - los pipelines de ejemplo pasan `ci-image.sh check-paths` (trigger.paths cubre
    servicePath + dependsOn).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
BASE = ROOT / "templates" / "gitops" / "azure-pipelines" / "pipelines"
FILES = sorted(BASE.rglob("*.yml"))

PARAM_USE = re.compile(r"parameters\.([A-Za-z_][A-Za-z0-9_]*)")


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _walk(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(k)
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _template_uses(node: Any) -> Iterator[dict]:
    for n in _walk(node):
        if isinstance(n, dict) and isinstance(n.get("template"), str):
            yield n


def _declared(doc: Any) -> dict[str, dict]:
    return {p["name"]: p for p in (doc or {}).get("parameters", []) if isinstance(p, dict)}


def _scripts(node: Any) -> Iterator[str]:
    for n in _walk(node):
        if not isinstance(n, dict):
            continue
        if isinstance(n.get("bash"), str):
            yield n["bash"]
        inputs = n.get("inputs")
        if isinstance(inputs, dict) and inputs.get("scriptType") == "bash" and isinstance(inputs.get("inlineScript"), str):
            yield inputs["inlineScript"]


def _neutralize(script: str) -> str:
    """Reemplaza ${{ … }} y $(…) de Azure por valores dummy aptos para bash -n."""
    s = re.sub(r"\$\{\{.*?\}\}", "AZURE_EXPR", script)
    # $(Var.Name) macro de Azure (sin espacios); deja $(cmd args) de bash intacto
    s = re.sub(r"\$\(([A-Za-z_][A-Za-z0-9_.]*)\)", "AZURE_MACRO", s)
    return s


class TestAzureTemplates(unittest.TestCase):
    def test_files_present(self) -> None:
        self.assertGreaterEqual(len(FILES), 9)

    def test_yaml_parses(self) -> None:
        for f in FILES:
            with self.subTest(file=f.relative_to(BASE)):
                self.assertIsNotNone(_load(f))

    def test_template_refs_and_params(self) -> None:
        for f in FILES:
            for use in _template_uses(_load(f)):
                target = (f.parent / use["template"]).resolve()
                with self.subTest(file=f.relative_to(BASE), template=use["template"]):
                    self.assertTrue(target.is_file(), f"no existe {target}")
                    declared = _declared(_load(target))
                    passed = set((use.get("parameters") or {}).keys())
                    unknown = passed - set(declared)
                    self.assertFalse(unknown, f"parámetros no declarados en {target.name}: {unknown}")
                    required = {n for n, p in declared.items() if "default" not in p}
                    self.assertFalse(required - passed, f"faltan parámetros obligatorios: {required - passed}")

    def test_parameter_references_declared(self) -> None:
        for f in FILES:
            doc = _load(f)
            declared = set(_declared(doc))
            if not declared:
                continue
            used = set(PARAM_USE.findall(f.read_text(encoding="utf-8").split("\nparameters:", 1)[-1]))
            # quitar referencias dentro de la propia sección parameters (nombres de keys)
            with self.subTest(file=f.relative_to(BASE)):
                self.assertFalse(used - declared, f"usados sin declarar: {used - declared}")

    def test_inline_scripts_bash_syntax(self) -> None:
        for f in FILES:
            for i, script in enumerate(_scripts(_load(f))):
                with self.subTest(file=f.relative_to(BASE), script=i):
                    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as tmp:
                        tmp.write(_neutralize(script))
                    r = subprocess.run(["bash", "-n", tmp.name], capture_output=True, text=True)
                    Path(tmp.name).unlink()
                    self.assertEqual(r.returncode, 0, f"{r.stderr}\n---\n{script}")

    def test_example_pipelines_cover_dependencies(self) -> None:
        for f in sorted((BASE / "services").glob("*-ci.yml")) + sorted((BASE / "libs").glob("*-ci.yml")):
            doc = _load(f)
            for use in _template_uses(doc):
                params = use.get("parameters") or {}
                path = params.get("servicePath") or params.get("workingDirectory")
                if not path:
                    continue
                with self.subTest(file=f.relative_to(BASE), template=use["template"]):
                    r = subprocess.run(
                        ["bash", str(ROOT / "scripts" / "ci-image.sh"), "check-paths", str(f), path,
                         *params.get("dependsOn", [])],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(r.returncode, 0, r.stderr)

    def test_release_pipelines_tag_only(self) -> None:
        for f in sorted(BASE.glob("*/*-release.yml")):
            trig = _load(f)["trigger"]
            with self.subTest(file=f.relative_to(BASE)):
                self.assertEqual(trig["branches"], {"exclude": ["*"]})
                self.assertNotIn("paths", trig, "release por tag no debe depender de paths (ADR-0060 §6)")
                self.assertTrue(all(t.endswith("@*") for t in trig["tags"]["include"]))


if __name__ == "__main__":
    unittest.main()
