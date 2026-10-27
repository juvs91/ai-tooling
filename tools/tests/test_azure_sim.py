"""
test_azure_sim.py — Suite de tests para tools/azure-sim (simulador de Azure Pipelines).

Corre con stdlib unittest + PyYAML:

  python3 -m unittest discover -s tools/tests -p test_azure_sim.py -v

Sin Docker: expresiones, compilación de los templates REALES, triggers y el flujo
completo merge → dev → rc → prod corrido por el runner con los YAML y scripts
reales, donde solo az/docker/uv/pip son fakes.
Con Docker (AZURE_SIM=1 y el stack arriba: tools/azure-sim/sim up): los 9
escenarios end-to-end contra registry/feed reales.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SIM_DIR = ROOT / "tools" / "azure-sim"
sys.path.insert(0, str(SIM_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from azsim import fixture  # noqa: E402
from azsim.compile import CompileError, Compiler  # noqa: E402
from azsim.expr import Context, ExprError, eval_text, truthy  # noqa: E402
from azsim.runtime import ci_triggers  # noqa: E402
from azsim.sim import LocalExecutor, Sim  # noqa: E402
from test_ci_acr import FAKE  # noqa: E402
from test_ci_pylib import FAKE_PYTHON, FAKE_UV  # noqa: E402

TEMPLATES_ROOT = ROOT / "templates" / "gitops" / "azure-pipelines"
SVC_CI = "pipelines/services/example-ventas-api-ci.yml"
SVC_REL = "pipelines/services/example-ventas-api-release.yml"


class TestExpr(unittest.TestCase):
    def ev(self, text: str, **names):
        return eval_text(text, Context(names))

    def test_eq_is_case_insensitive_and_coerces_bool(self) -> None:
        self.assertTrue(self.ev("eq('Refs/Heads/Main', 'refs/heads/main')"))
        self.assertTrue(self.ev("eq(variables.X, 'True')", variables={"X": True}))

    def test_string_false_is_truthy_like_azure(self) -> None:
        self.assertTrue(truthy("false"))
        self.assertFalse(truthy(""))

    def test_property_and_index_access(self) -> None:
        deps = {"Resolve": {"outputs": {"resolve.tag.env": "prod"}}}
        self.assertEqual(self.ev("dependencies.Resolve.outputs['resolve.tag.env']", dependencies=deps), "prod")
        self.assertIsNone(self.ev("dependencies.Nope.outputs['x']", dependencies=deps))

    def test_functions(self) -> None:
        self.assertTrue(self.ev("in('hotfix', 'prod', 'hotfix')"))
        self.assertEqual(self.ev("join(' ', parameters.d)", parameters={"d": ["a", "b"]}), "a b")
        self.assertEqual(self.ev("length(parameters.d)", parameters={"d": []}), 0)
        self.assertTrue(self.ev("startsWith('refs/tags/x', 'refs/tags/')"))

    def test_unknown_function_is_an_error(self) -> None:
        with self.assertRaises(ExprError):
            self.ev("counter('x', 1)")


class TestCompileRealTemplates(unittest.TestCase):
    def compile(self, rel: str) -> dict:
        return Compiler(TEMPLATES_ROOT).compile_pipeline(TEMPLATES_ROOT / rel)

    def test_every_example_compiles(self) -> None:
        for f in sorted((TEMPLATES_ROOT / "pipelines").glob("*/*.yml")):
            with self.subTest(file=f.name):
                self.assertTrue(self.compile(str(f.relative_to(TEMPLATES_ROOT)))["stages"])

    def test_release_each_and_else(self) -> None:
        stages = {s["stage"]: s for s in self.compile(SVC_REL)["stages"]}
        self.assertEqual(list(stages), ["Resolve", "Publish", "Deploy_dev", "Deploy_qa", "Deploy_prod"])
        self.assertIn("'prod', 'hotfix'", stages["Deploy_prod"]["condition"])
        self.assertIn("'qa')", stages["Deploy_qa"]["condition"])
        steps = stages["Deploy_dev"]["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"]
        self.assertEqual(steps[0], {"checkout": "self", "fetchDepth": 1})

    def test_ci_conditional_steps_and_params(self) -> None:
        job = self.compile(SVC_CI)["stages"][0]["jobs"][1]
        self.assertEqual(job["variables"]["CI_DEPENDS_ON"], "_shared/libs/auth")
        self.assertEqual(job["dependsOn"], ["python_ci"])
        self.assertTrue(job["steps"][0]["persistCredentials"])  # or(autoDevTag, autoHotfixTag)

    def test_unknown_or_missing_parameter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(TEMPLATES_ROOT / "pipelines", root / "pipelines")
            p = root / SVC_CI
            p.write_text(p.read_text().replace("dependsOnJobs: [python_ci]", "dependsOnJob: [python_ci]"))
            with self.assertRaisesRegex(CompileError, "no declarados"):
                Compiler(root).compile_pipeline(p)
            p.write_text(p.read_text().replace("          acrName: metalsaacr", ""))
            with self.assertRaises(CompileError):
                Compiler(root).compile_pipeline(p)


class TestTriggers(unittest.TestCase):
    CI = {"branches": {"include": ["main", "hotfix/ventas-api/*"]},
          "paths": {"include": ["ventas/api", "_shared/libs/auth"]}}
    REL = {"branches": {"exclude": ["*"]}, "tags": {"include": ["ventas-api@*"]}}

    def test_paths_filter(self) -> None:
        self.assertTrue(ci_triggers(self.CI, "refs/heads/main", ["ventas/api/app.py"]))
        self.assertTrue(ci_triggers(self.CI, "refs/heads/main", ["_shared/libs/auth/x.py"]))
        self.assertFalse(ci_triggers(self.CI, "refs/heads/main", ["ventas/web/x"]))
        self.assertFalse(ci_triggers(self.CI, "refs/heads/main", ["ventas/apiv2/x"]))

    def test_branches(self) -> None:
        self.assertTrue(ci_triggers(self.CI, "refs/heads/hotfix/ventas-api/x", ["ventas/api/a"]))
        self.assertFalse(ci_triggers(self.CI, "refs/heads/feature/x", ["ventas/api/a"]))

    def test_tags_only_release(self) -> None:
        self.assertTrue(ci_triggers(self.REL, "refs/tags/ventas-api@1.0.0-rc.1", []))
        self.assertFalse(ci_triggers(self.REL, "refs/tags/auth@1.0.0", []))
        self.assertFalse(ci_triggers(self.REL, "refs/heads/main", ["ventas/api/a"]))
        self.assertFalse(ci_triggers(self.CI, "refs/tags/ventas-api@1.0.0", []), "CI no corre por tags")


class SimLocalTestCase(unittest.TestCase):
    """Runner + YAML + scripts reales; az/docker/uv/pip fakes en el host."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        bindir = tmp / "bin"
        bindir.mkdir()
        for name, body in (("az", FAKE), ("docker", FAKE), ("python", FAKE_PYTHON), ("uv", FAKE_UV)):
            (bindir / name).write_text(body)
            (bindir / name).chmod(0o755)
        self.state = tmp / "acr.json"
        self.state.write_text(json.dumps({"repos": {}, "locks": [], "calls": [], "pushes": 0}))
        (tmp / "calls.log").write_text("")
        env = {"PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_ACR_STATE": str(self.state),
               "FAKE_LOG": str(tmp / "calls.log")}
        (tmp / "sim").mkdir()
        self.sim = Sim(tmp / "sim", LocalExecutor(), env)
        self.sim.init_repo()
        fixture.write(self.sim.dev)
        self.sim.commit({}, "init")
        self.sim.git("push", "-q", "origin", "main")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def acr(self) -> dict:
        return json.loads(self.state.read_text())["repos"].get("ventas/api", {})

    def touch_service(self, marker: str) -> None:
        src = fixture.SERVICE["ventas/api/ventas_api.py"]
        self.sim.commit({"ventas/api/ventas_api.py": f"{src}\n\nMARKER = {marker!r}\n"}, marker)


class TestSimFlow(SimLocalTestCase):
    def test_merge_dev_rc_prod_same_digest(self) -> None:
        self.touch_service("feature")
        runs = self.sim.push("main")
        self.assertEqual([r.pipeline for r in runs], [SVC_CI, SVC_REL])
        self.assertTrue(all(r.result == "Succeeded" for r in runs), [r.log for r in runs])
        self.assertEqual([e for e, _ in runs[1].deployments()], ["ventas-api-dev"])
        ctx = [t for t in self.acr() if t.startswith("ctx-")]
        self.assertEqual(len(ctx), 1)

        rc = self.sim.push_tag("ventas-api@1.0.0-rc.1")[0]
        self.assertEqual([e for e, _ in rc.deployments()], ["ventas-api-qa"])
        self.sim.commit({"ventas/web/package.json": "{}\n"}, "web")
        self.assertEqual(self.sim.push("main"), [], "commit ajeno al servicio no dispara nada")
        prod = self.sim.push_tag("ventas-api@1.0.0")[0]
        self.assertEqual(prod.result, "Succeeded", prod.log)
        self.assertEqual([e for e, _ in prod.deployments()], ["ventas-api-prod"])
        acr = self.acr()
        self.assertEqual(acr["1.0.0"], acr["1.0.0-rc.1"])
        self.assertEqual(acr["1.0.0"], acr[ctx[0]])

    def test_pr_validates_without_publishing(self) -> None:
        self.sim.git("checkout", "-q", "-b", "feature/x")
        self.touch_service("pr")
        run = self.sim.pull_request("feature/x", "main", [SVC_CI])[0]
        self.assertEqual(run.result, "Succeeded", run.log)
        self.assertEqual(run.step("CI", "acr_build", "ensure-ctx → ACR").result, "Skipped")
        self.assertEqual(run.step("CI", "acr_build", "docker build (validación, sin push)").result, "Succeeded")
        self.assertEqual(self.acr(), {})

    def test_hotfix_branch_tags_and_deploys_prod(self) -> None:
        self.touch_service("base")
        self.sim.push("main")
        self.sim.push_tag("ventas-api@1.0.0-rc.1")
        self.sim.push_tag("ventas-api@1.0.0")
        self.sim.git("checkout", "-q", "-b", "hotfix/ventas-api/fix", "ventas-api@1.0.0")
        self.touch_service("hotfix")
        runs = self.sim.push("hotfix/ventas-api/fix")
        self.assertIn("ventas-api@1.0.0-hotfix.1", self.sim.remote_tags())
        rel = [r for r in runs if r.pipeline == SVC_REL]
        self.assertEqual([e for e, _ in rel[0].deployments()], ["ventas-api-prod"])
        self.assertNotEqual(self.acr()["1.0.0-hotfix.1"], self.acr()["1.0.0"])

    def test_without_persist_credentials_tag_push_fails(self) -> None:
        """Fidelidad: sin persistCredentials el agente de Azure no puede empujar tags."""
        ci = self.sim.dev / "pipelines/templates/jobs/acr-build.yml"
        ci.write_text(ci.read_text().replace(
            "persistCredentials: ${{ or(parameters.autoDevTag, parameters.autoHotfixTag) }}",
            "persistCredentials: false"))
        self.touch_service("sin-credenciales")
        runs = self.sim.push("main")
        self.assertEqual(runs[0].result, "Failed")
        self.assertEqual(runs[0].step("CI", "acr_build", "Tag -dev automático").result, "Failed")


@unittest.skipUnless(os.environ.get("AZURE_SIM") == "1", "AZURE_SIM=1 + `tools/azure-sim/sim up` para el e2e con Docker")
class TestSimDocker(unittest.TestCase):
    def test_all_scenarios(self) -> None:
        r = subprocess.run([str(SIM_DIR / "sim"), "run", "all"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-6000:] + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
