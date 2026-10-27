"""Ejecución de un pipeline ya compilado, con la semántica de runtime de Azure
Pipelines que usan los templates:

  - stages en orden con dependsOn (default: el anterior) y condition (default
    succeeded()); jobs con dependsOn dentro del stage; steps con condition.
  - variables del job con $[ … ] (stageDependencies / dependencies), macros
    $(var) en scripts, env y task inputs.
  - variables NO secretas → env (MAYÚSCULAS, '.' → '_'); System.AccessToken es
    secreta y solo llega si el step la mapea en env: (igual que Azure).
  - ##vso[task.setvariable …;isOutput=true] → $(step.var), STEP_VAR en env,
    stageDependencies/dependencies outputs; ##vso[task.logissue] se reporta.
  - checkout: self con fetchDepth/fetchTags/persistCredentials (sin
    persistCredentials el push al origin falla, como en Azure). Jobs normales
    sin checkout explícito reciben el implícito (depth 1, sin tags); los jobs
    deployment no hacen checkout implícito.
  - tasks: AzureCLI@2, UsePythonVersion@0, PublishTestResults@2,
    TwineAuthenticate@1. Cualquier otra → RuntimeError_.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .expr import Context, ExprError, eval_text, to_str, truthy

MACRO = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_.\-]*)\)")
RUNTIME_EXPR = re.compile(r"^\s*\$\[(.*)\]\s*$", re.S)
VSO = re.compile(r"##vso\[(?P<cmd>[\w.]+)(?P<props>[^\]]*)\](?P<value>.*)")
SECRET_VARS = {"system.accesstoken"}


class RuntimeError_(Exception):
    pass


@dataclass
class StepResult:
    name: str
    display: str
    result: str  # Succeeded | Failed | Skipped
    exit_code: int | None = None
    output: str = ""


@dataclass
class JobResult:
    name: str
    result: str = "Succeeded"
    steps: list[StepResult] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)  # "step.var" → valor
    environment: str | None = None
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class StageResult:
    name: str
    result: str = "Succeeded"
    jobs: dict[str, JobResult] = field(default_factory=dict)


@dataclass
class RunResult:
    pipeline: str
    source_branch: str
    build_id: int
    result: str = "Succeeded"
    stages: dict[str, StageResult] = field(default_factory=dict)
    issues: list[tuple[str, str]] = field(default_factory=list)  # (type, mensaje)
    log: list[str] = field(default_factory=list)

    def step(self, stage: str, job: str, display: str) -> StepResult:
        for s in self.stages[stage].jobs[job].steps:
            if s.display == display:
                return s
        raise KeyError(f"{stage}/{job}/{display}")

    def deployments(self) -> list[tuple[str, str]]:
        """[(environment, imagen)] de los jobs deployment que corrieron."""
        out = []
        for st in self.stages.values():
            for j in st.jobs.values():
                if j.environment and j.result == "Succeeded":
                    out.append((j.environment, j.variables.get("CI_IMAGE_REF", "")))
        return out


@dataclass
class Event:
    """Lo que dispara un run: rama/tag/PR y el commit a construir."""
    source_branch: str      # refs/heads/main · refs/tags/x@1.0.0 · refs/pull/1/merge
    commit: str
    reason: str = "IndividualCI"   # IndividualCI | BatchedCI | PullRequest | Manual


class Executor:
    """Ejecuta un script bash en el 'agente'. Implementaciones: local / docker."""

    def run(self, script: str, cwd: Path, env: dict[str, str]) -> tuple[int, str]:  # pragma: no cover
        raise NotImplementedError


class Runner:
    def __init__(self, executor: Executor, origin: Path, work: Path,
                 sim_env: dict[str, str] | None = None,
                 pypi_url: str = "http://localhost:8081/",
                 collection_uri: str = "https://dev.azure.com/sim/",
                 echo: Callable[[str], None] | None = None) -> None:
        self.executor = executor
        self.origin = origin
        self.work = work
        self.sim_env = sim_env or {}
        self.pypi_url = pypi_url
        self.collection_uri = collection_uri
        self.echo = echo or (lambda s: None)

    # ── API ──────────────────────────────────────────────────────────────────
    def run(self, pipeline: dict, pipeline_name: str, event: Event, build_id: int) -> RunResult:
        res = RunResult(pipeline_name, event.source_branch, build_id)
        self._log(res, f"══ run {build_id} · {pipeline_name} · {event.source_branch} @ {event.commit[:10]}")
        predefined = self._predefined(event, build_id, pipeline_name)
        pipe_vars = {**predefined, **self._vars_literal(pipeline.get("variables"))}
        stages = pipeline.get("stages")
        if stages is None:
            jobs = pipeline.get("jobs") or [{"job": "Job", "steps": pipeline.get("steps") or []}]
            stages = [{"stage": "__default", "jobs": jobs}]
        prev = None
        for st in stages:
            name = st["stage"]
            deps = st.get("dependsOn", [prev] if prev else [])
            deps = [deps] if isinstance(deps, str) else list(deps or [])
            prev = name
            sres = StageResult(name)
            res.stages[name] = sres
            dep_results = [res.stages[d].result for d in deps if d in res.stages]
            ctx = Context(
                {"variables": pipe_vars, "dependencies": self._stage_deps(res, deps)},
                self._status(dep_results),
            )
            cond = st.get("condition", "succeeded()")
            if not self._cond(cond, ctx, f"stage {name}"):
                sres.result = "Skipped"
                self._log(res, f"── stage {name}: skipped ({_oneline(cond)})")
                continue
            self._log(res, f"── stage {name}")
            self._run_stage(st, sres, res, pipe_vars, event)
        res.result = "Failed" if any(s.result == "Failed" for s in res.stages.values()) else "Succeeded"
        self._log(res, f"══ run {build_id}: {res.result}")
        return res

    # ── stages / jobs ────────────────────────────────────────────────────────
    def _run_stage(self, st: dict, sres: StageResult, res: RunResult, pipe_vars: dict, event: Event) -> None:
        pending = list(st.get("jobs") or [])
        while pending:
            progressed = False
            for job in list(pending):
                jname = job.get("job") or job.get("deployment")
                deps = job.get("dependsOn", [])
                deps = [deps] if isinstance(deps, str) else list(deps or [])
                if any(d not in sres.jobs for d in deps):
                    continue
                pending.remove(job)
                progressed = True
                jres = JobResult(jname)
                sres.jobs[jname] = jres
                dep_results = [sres.jobs[d].result for d in deps]
                ctx = Context({"variables": pipe_vars,
                               "dependencies": {d: {"result": sres.jobs[d].result,
                                                    "outputs": sres.jobs[d].outputs} for d in deps}},
                              self._status(dep_results))
                cond = job.get("condition", "succeeded()")
                if not self._cond(cond, ctx, f"job {jname}"):
                    jres.result = "Skipped"
                    self._log(res, f"   job {jname}: skipped")
                    continue
                self._run_job(job, jres, res, pipe_vars, event)
            if not progressed:
                raise RuntimeError_(f"dependsOn circular o inexistente en stage {sres.name}: {[j.get('job') for j in pending]}")
        if any(j.result == "Failed" for j in sres.jobs.values()):
            sres.result = "Failed"

    def _run_job(self, job: dict, jres: JobResult, res: RunResult, pipe_vars: dict, event: Event) -> None:
        is_deploy = "deployment" in job
        if is_deploy:
            jres.environment = self._env_name(job.get("environment"))
            strat = job.get("strategy") or {}
            if set(strat) != {"runOnce"}:
                raise RuntimeError_(f"estrategia de deployment no soportada: {sorted(strat)}")
            hooks = strat["runOnce"]
            unsupported = set(hooks) - {"deploy"}
            if unsupported:
                raise RuntimeError_(f"lifecycle hooks no soportados: {sorted(unsupported)}")
            steps = list(hooks["deploy"].get("steps") or [])
            self._log(res, f"   deployment {jres.name} → environment {jres.environment} (approvals: auto-aprobado en el simulador)")
        else:
            steps = list(job.get("steps") or [])
            if not any("checkout" in s for s in steps):
                steps.insert(0, {"checkout": "self"})  # checkout implícito de Azure
            self._log(res, f"   job {jres.name}")

        stage_ctx_deps = self._stage_deps_for_jobvars(res)
        jvars: dict[str, str] = dict(pipe_vars)
        ctx = Context({"variables": jvars, "stageDependencies": stage_ctx_deps,
                       "dependencies": {}}, {})
        for k, v in self._vars_raw(job.get("variables")).items():
            m = RUNTIME_EXPR.match(v) if isinstance(v, str) else None
            if m:
                try:
                    jvars[k] = to_str(eval_text(m.group(1), ctx))
                except ExprError as e:
                    raise RuntimeError_(f"job {jres.name}: variable {k}: {e}") from e
            else:
                jvars[k] = self._macro(to_str(v), jvars)
        jres.variables = {k: v for k, v in jvars.items() if k not in pipe_vars or pipe_vars[k] != v}

        job_dir = self.work / f"{res.build_id}" / f"{jres.name}"
        src = job_dir / "s"
        tmp = job_dir / "_temp"
        tmp.mkdir(parents=True, exist_ok=True)
        jvars["Agent.TempDirectory"] = str(tmp)
        jvars["Build.SourcesDirectory"] = str(src)
        jvars["System.DefaultWorkingDirectory"] = str(src)

        failed = False
        for i, step in enumerate(steps):
            sname = step.get("name", f"__step{i}")
            display = step.get("displayName") or step.get("task") or ("checkout" if "checkout" in step else sname)
            ctx = Context({"variables": jvars}, self._status(["Failed" if failed else "Succeeded"]))
            cond = step.get("condition", "succeeded()")
            if not self._cond(cond, ctx, f"step {display}"):
                jres.steps.append(StepResult(sname, display, "Skipped"))
                self._log(res, f"     · {display}: skipped")
                continue
            code, out = self._run_step(step, jvars, src, tmp, event, res, is_deploy)
            for line in out.splitlines():
                self._vso(line, sname, jvars, jres, res)
            result = "Succeeded" if code == 0 else "Failed"
            jres.steps.append(StepResult(sname, display, result, code, out))
            self._log(res, f"     {'✓' if code == 0 else '✗'} {display}" + ("" if code == 0 else f" (exit {code})"))
            if code != 0:
                for line in out.strip().splitlines()[-15:]:
                    self._log(res, f"         | {line}")
                failed = True
        if failed:
            jres.result = "Failed"

    # ── steps ────────────────────────────────────────────────────────────────
    def _run_step(self, step: dict, jvars: dict, src: Path, tmp: Path, event: Event,
                  res: RunResult, is_deploy: bool) -> tuple[int, str]:
        env = self._env(jvars, step.get("env") or {})
        wd = src
        if step.get("workingDirectory"):
            wd = src / self._macro(str(step["workingDirectory"]), jvars)
        if "checkout" in step:
            return self._checkout(step, src, event)
        if "bash" in step:
            return self.executor.run(self._macro(step["bash"], jvars), wd, env)
        if "script" in step or "pwsh" in step or "powershell" in step:
            raise RuntimeError_("steps script/pwsh/powershell no soportados (los templates usan bash)")
        task = str(step.get("task", ""))
        inputs = {k: self._macro(to_str(v), jvars) if not isinstance(v, (list, dict)) else v
                  for k, v in (step.get("inputs") or {}).items()}
        if task.startswith("AzureCLI@"):
            if inputs.get("scriptType") != "bash":
                raise RuntimeError_("AzureCLI: solo scriptType bash")
            if inputs.get("scriptLocation") == "scriptPath":
                script = f"bash {shlex.quote(inputs['scriptPath'])} {inputs.get('arguments', '')}"
            else:
                script = inputs.get("inlineScript", "")
            if not inputs.get("azureSubscription"):
                return 1, "AzureCLI: falta azureSubscription"
            # El agente real hace az login con la service connection; aquí el az es el shim del simulador.
            return self.executor.run(script, wd, env)
        if task.startswith("UsePythonVersion@"):
            return self.executor.run('python --version && echo "UsePythonVersion: python del agente (toolcache simulado)"', src, env)
        if task.startswith("PublishTestResults@"):
            files = inputs.get("testResultsFiles", "")
            # Azure: sin archivos solo avisa, salvo failTaskOnMissingResultsFile: true
            strict = truthy(inputs.get("failTaskOnMissingResultsFile", False))
            missing = ("exit 1" if strict
                       else f"echo '##vso[task.logissue type=warning]No test result files matching {files}'")
            return self.executor.run(
                f"if ls {files} >/dev/null 2>&1; then ls -l {files}; else {missing}; fi", src, env)
        if task.startswith("TwineAuthenticate@"):
            feed = inputs.get("artifactFeed", "")
            name = feed.split("/")[-1]
            pypirc = tmp / ".pypirc"
            script = (
                f"cat > {shlex.quote(str(pypirc))} <<'EOF'\n[distutils]\nindex-servers = {name}\n\n"
                f"[{name}]\nrepository = {self.pypi_url}\nusername = sim\npassword = sim\nEOF\n"
                f"echo '##vso[task.setvariable variable=PYPIRC_PATH]{pypirc}'"
            )
            return self.executor.run(script, src, env)
        raise RuntimeError_(f"task no soportada por el simulador: {task!r}")

    def _checkout(self, step: dict, src: Path, event: Event) -> tuple[int, str]:
        if step["checkout"] != "self":
            raise RuntimeError_(f"checkout {step['checkout']!r} no soportado")
        depth = int(step.get("fetchDepth", 1))
        tags = truthy(step.get("fetchTags", False))
        persist = truthy(step.get("persistCredentials", False))
        url = f"file://{self.origin}"
        ref = event.source_branch
        lines = [
            "set -e",
            f"rm -rf {shlex.quote(str(src))}",
            f"git init -q {shlex.quote(str(src))}",
            f"cd {shlex.quote(str(src))}",
            f"git remote add origin {shlex.quote(url)}",
            # como el agente: fetch del ref disparador (+ tags si fetchTags)
            "git fetch -q " + ("--tags " if tags else "--no-tags ")
            + (f"--depth {depth} " if depth > 0 else "")
            + f"origin {shlex.quote('+' + ref + ':refs/remotes/origin/__trigger')}",
            f"git checkout -q --detach {shlex.quote(event.commit)}",
        ]
        if not persist:
            # Sin persistCredentials el token no queda en el repo → push falla
            lines.append("git remote set-url --push origin file:///nonexistent/no-persistCredentials")
        lines.append("echo checkout: $(git rev-parse --short HEAD) depth=" + str(depth) + " tags=" + str(tags).lower())
        return self.executor.run("\n".join(lines), self.work, dict(self.sim_env))

    # ── variables / expresiones ──────────────────────────────────────────────
    def _predefined(self, event: Event, build_id: int, pipeline: str) -> dict[str, str]:
        branch = event.source_branch
        return {
            "Build.SourceBranch": branch,
            "Build.SourceBranchName": branch.rsplit("/", 1)[-1],
            "Build.SourceVersion": event.commit,
            "Build.BuildId": str(build_id),
            "Build.BuildNumber": f"sim.{build_id}",
            "Build.Reason": event.reason,
            "Build.DefinitionName": pipeline,
            "Build.Repository.Uri": f"https://dev.azure.com/sim/project/_git/{self.origin.stem}",
            "System.CollectionUri": self.collection_uri,
            "System.AccessToken": "sim-access-token",
            "System.TeamProject": "project",
            "TF_BUILD": "True",
        }

    @staticmethod
    def _vars_raw(v: Any) -> dict[str, Any]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return dict(v)
        out = {}
        for item in v:
            if "name" in item:
                out[item["name"]] = item.get("value", "")
            else:
                raise RuntimeError_(f"variables de grupo/template no soportadas: {item}")
        return out

    def _vars_literal(self, v: Any) -> dict[str, str]:
        return {k: to_str(x) for k, x in self._vars_raw(v).items()}

    @staticmethod
    def _macro(text: str, jvars: dict[str, str]) -> str:
        lower = {k.lower(): v for k, v in jvars.items()}
        return MACRO.sub(lambda m: lower.get(m.group(1).lower(), m.group(0)), text)

    def _env(self, jvars: dict[str, str], step_env: dict) -> dict[str, str]:
        env = dict(self.sim_env)
        for k, v in jvars.items():
            if k.lower() in SECRET_VARS:
                continue
            env[k.upper().replace(".", "_").replace("-", "_")] = v
        for k, v in step_env.items():
            env[str(k)] = self._macro(to_str(v), jvars)
        return env

    def _cond(self, cond: str, ctx: Context, where: str) -> bool:
        try:
            return truthy(eval_text(str(cond), ctx))
        except ExprError as e:
            raise RuntimeError_(f"{where}: condition {cond!r}: {e}") from e

    @staticmethod
    def _status(dep_results: list[str]) -> dict[str, Callable[[], bool]]:
        ok = all(r in ("Succeeded", "SucceededWithIssues", "Skipped") for r in dep_results)
        any_failed = any(r == "Failed" for r in dep_results)
        return {
            "succeeded": lambda: ok,
            "failed": lambda: any_failed,
            "succeededorfailed": lambda: True,
            "always": lambda: True,
            "canceled": lambda: False,
        }

    @staticmethod
    def _stage_deps(res: RunResult, deps: list[str]) -> dict:
        out = {}
        for d in deps:
            st = res.stages.get(d)
            if st is None:
                continue
            outputs = {f"{j}.{k}": v for j, jr in st.jobs.items() for k, v in jr.outputs.items()}
            out[d] = {"result": st.result, "outputs": outputs}
        return out

    @staticmethod
    def _stage_deps_for_jobvars(res: RunResult) -> dict:
        return {s: {j: {"result": jr.result, "outputs": jr.outputs} for j, jr in st.jobs.items()}
                for s, st in res.stages.items()}

    @staticmethod
    def _env_name(env: Any) -> str:
        if isinstance(env, dict):
            return str(env.get("name"))
        return str(env)

    def _vso(self, line: str, step_name: str, jvars: dict, jres: JobResult, res: RunResult) -> None:
        m = VSO.search(line)
        if not m:
            return
        props = dict(p.split("=", 1) for p in m.group("props").strip().split(";") if "=" in p)
        props = {k.strip().lower(): v.strip() for k, v in props.items()}
        cmd, value = m.group("cmd"), m.group("value").rstrip("\r")
        if cmd == "task.setvariable":
            var = props.get("variable")
            if not var:
                raise RuntimeError_(f"setvariable sin variable: {line}")
            if props.get("isoutput", "").lower() == "true":
                if step_name.startswith("__step"):
                    res.issues.append(("warning", f"output {var} de un step sin name: — inaccesible"))
                jres.outputs[f"{step_name}.{var}"] = value
                jvars[f"{step_name}.{var}"] = value
            else:
                jvars[var] = value
        elif cmd == "task.logissue":
            res.issues.append((props.get("type", "warning"), value))
        else:
            raise RuntimeError_(f"logging command no soportado: {cmd}")

    def _log(self, res: RunResult, msg: str) -> None:
        res.log.append(msg)
        self.echo(msg)


def _oneline(s: str) -> str:
    return " ".join(str(s).split())


# ── triggers ──────────────────────────────────────────────────────────────────

def _match_ref(patterns: list[str], ref: str, prefix: str) -> bool:
    short = ref[len(prefix):] if ref.startswith(prefix) else ref
    for p in patterns:
        p = str(p)
        full = p if p.startswith("refs/") else prefix + p
        if fnmatch.fnmatchcase(ref, full) or fnmatch.fnmatchcase(short, p):
            return True
    return False


def _filter(spec: Any, ref: str, prefix: str) -> bool:
    if spec is None:
        return True
    if isinstance(spec, list):
        return _match_ref(spec, ref, prefix)
    inc = spec.get("include")
    exc = spec.get("exclude") or []
    included = True if inc is None else _match_ref(inc, ref, prefix)
    return included and not _match_ref(exc, ref, prefix)


def _path_match(patterns: list[str], path: str) -> bool:
    for p in patterns:
        p = str(p).rstrip("/")
        if "*" in p:
            if fnmatch.fnmatchcase(path, p):
                return True
        elif path == p or path.startswith(p + "/"):
            return True
    return False


def ci_triggers(trigger: Any, ref: str, changed: list[str]) -> bool:
    """¿Este `trigger:` dispara un run para un push a `ref` con estos archivos?

    Semántica documentada de Azure: `none` desactiva; ausente = todas las ramas;
    lista = ramas; mapping con branches/tags/paths. Para tags NO se evalúan
    paths (Learn no lo documenta — ADR-0060 §6 evita depender de eso).
    """
    if trigger == "none":
        return False
    if ref.startswith("refs/tags/"):
        if not isinstance(trigger, dict):
            return False
        tags = trigger.get("tags")
        if tags is None:
            return False
        return _filter(tags, ref, "refs/tags/")
    if trigger is None:
        return True
    if isinstance(trigger, list):
        return _match_ref(trigger, ref, "refs/heads/")
    if not _filter(trigger.get("branches"), ref, "refs/heads/"):
        return False
    paths = trigger.get("paths")
    if paths:
        inc = paths.get("include")
        exc = paths.get("exclude") or []
        hit = [f for f in changed if (inc is None or _path_match(inc, f)) and not _path_match(exc, f)]
        return bool(hit)
    return True
