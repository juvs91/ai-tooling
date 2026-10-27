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
  - el YAML solo cablea (ADR-0061): cada step `bash:` / `AzureCLI@2` es una única
    llamada `scripts/ci-*.sh <comando>` y el comando existe en el script;
  - cada variable que el comando exige con `ci_require` llega por el `env:` del
    step, las variables del job o las predefinidas del agente;
  - cada `$(step.var)` y `stageDependencies.S.j.outputs['step.var']` apunta a un
    step con ese `name:`;
  - los pipelines de ejemplo pasan `ci-image.sh check-paths` (trigger.paths cubre
    servicePath + dependsOn).
"""

from __future__ import annotations

import re
import subprocess
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


STEP_CALL = re.compile(r"^bash (scripts/ci-[a-z-]+\.sh) ([a-z-]+)$")
REQUIRE = re.compile(r"ci_require ([A-Z0-9_ ]+)")
STEP_OUTPUT = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\)")
STAGE_OUTPUT = re.compile(r"stageDependencies\.(\w+)\.(\w+)\.outputs\['(\w+)\.(\w+)'\]")

# Variables que pone el agente (o una task anterior del mismo job) sin pasar por el YAML.
PREDEFINED = {
    "BUILD_SOURCEBRANCH", "BUILD_SOURCEBRANCHNAME", "BUILD_BUILDID", "BUILD_BUILDNUMBER",
    "BUILD_SOURCEVERSION", "BUILD_REPOSITORY_URI", "AGENT_TEMPDIRECTORY",
    "SYSTEM_COLLECTIONURI",
    "PYPIRC_PATH",  # TwineAuthenticate@1
}
# $(System.AccessToken), $(Build.BuildId)… son predefinidas, no salidas de un step.
PREDEFINED_NAMESPACES = {"System", "Build", "Agent", "Pipeline", "Environment"}


def _script_call(step: dict) -> tuple[str, str] | None:
    """(script, comando) de un step bash/AzureCLI@2; None si no es un step de script."""
    if isinstance(step.get("bash"), str):
        m = STEP_CALL.match(step["bash"].strip())
        return (m.group(1), m.group(2)) if m else ("<inline>", step["bash"])
    if str(step.get("task", "")).startswith("AzureCLI@"):
        inp = step.get("inputs") or {}
        if inp.get("scriptLocation") != "scriptPath":
            return ("<inline>", str(inp.get("inlineScript", "")))
        return (inp.get("scriptPath", ""), str(inp.get("arguments", "")).strip())
    return None


def _jobs(doc: Any) -> Iterator[tuple[dict, list[dict]]]:
    """(job, steps aplanados) — incluye steps de deployment y de ramas ${{ if }}."""
    for n in _walk(doc):
        if isinstance(n, dict) and ("job" in n or "deployment" in n):
            steps = [s for s in _walk(n.get("steps") or n.get("strategy") or [])
                     if isinstance(s, dict) and ("bash" in s or "task" in s or "checkout" in s)]
            yield n, steps


def _required(script: str, command: str) -> set[str]:
    text = (ROOT / script).read_text(encoding="utf-8")
    func = "cmd_" + command.replace("-", "_")
    m = re.search(rf"^{func}\(\) \{{\n(.*?)^\}}", text, re.S | re.M)
    if not m:
        raise AssertionError(f"{script} no define {func}()")
    return {v for line in REQUIRE.findall(m.group(1)) for v in line.split()}


def _step_names(doc: Any) -> dict[str, set[str]]:
    """job → nombres de steps (name:)."""
    return {str(j.get("job") or j.get("deployment")): {s["name"] for s in steps if "name" in s}
            for j, steps in _jobs(doc)}


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

    def test_steps_only_call_scripts(self) -> None:
        for f in FILES:
            for job, steps in _jobs(_load(f)):
                for step in steps:
                    call = _script_call(step)
                    if call is None:
                        continue
                    script, command = call
                    with self.subTest(file=f.relative_to(BASE), step=step.get("displayName", command)):
                        self.assertNotEqual(script, "<inline>", f"lógica inline en el YAML:\n{command}")
                        self.assertRegex(script, r"^scripts/ci-[a-z-]+\.sh$")
                        self.assertTrue((ROOT / script).is_file(), f"no existe {script}")
                        main = (ROOT / script).read_text(encoding="utf-8")
                        self.assertRegex(main, rf"(?m)^\s+{re.escape(command)}\)", f"{script} no tiene el comando {command}")

    def test_script_inputs_wired(self) -> None:
        for f in FILES:
            for job, steps in _jobs(_load(f)):
                job_vars = set((job.get("variables") or {}).keys()) if isinstance(job.get("variables"), dict) else set()
                for step in steps:
                    call = _script_call(step)
                    if call is None or call[0] == "<inline>":
                        continue
                    available = job_vars | set((step.get("env") or {}).keys()) | PREDEFINED
                    with self.subTest(file=f.relative_to(BASE), step=f"{call[0]} {call[1]}"):
                        missing = _required(*call) - available
                        self.assertFalse(missing, f"variables exigidas por ci_require sin cablear: {missing}")

    def test_output_references_resolve(self) -> None:
        for f in FILES:
            doc = _load(f)
            names = _step_names(doc)
            text = f.read_text(encoding="utf-8")
            for job, steps in _jobs(doc):
                own = names[str(job.get("job") or job.get("deployment"))]
                for step in steps:
                    for ref_step, var in STEP_OUTPUT.findall(yaml.safe_dump(step.get("env") or {})):
                        if ref_step in PREDEFINED_NAMESPACES:
                            continue
                        with self.subTest(file=f.relative_to(BASE), ref=f"{ref_step}.{var}"):
                            self.assertIn(ref_step, own, f"$({ref_step}.{var}) no apunta a un step con name: {ref_step}")
            for stage, jobname, ref_step, var in STAGE_OUTPUT.findall(text):
                with self.subTest(file=f.relative_to(BASE), ref=f"{stage}.{jobname}.{ref_step}.{var}"):
                    self.assertIn(jobname, names, f"no hay job {jobname}")
                    self.assertIn(ref_step, names[jobname], f"el job {jobname} no tiene step name: {ref_step}")

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
