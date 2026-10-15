"""Orquestación tipo Azure DevOps sobre un repo git local.

  - Repo `origin` bare + un clon de trabajo del "desarrollador".
  - Pipelines registrados = pipelines/**/*.yml (sin templates/) que declaran
    `trigger:`; el YAML se lee del commit que se construye, como en Azure.
  - push de rama / tag → evalúa triggers → corre los pipelines que matchean →
    los tags que esos runs empujen al origin disparan nuevos runs (cola FIFO).
  - PR: crea refs/pull/<n>/merge y corre los pipelines indicados (Build
    validation es configuración de la branch policy, no del YAML).
"""

from __future__ import annotations

import os
import subprocess
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .compile import Compiler
from .runtime import Event, Executor, RunResult, Runner, ci_triggers


class LocalExecutor(Executor):
    """Corre los steps en el host (tests sin Docker; fakes vía PATH en sim_env)."""

    def run(self, script: str, cwd: Path, env: dict[str, str]) -> tuple[int, str]:
        cwd.mkdir(parents=True, exist_ok=True)
        full = {**os.environ, **env}
        r = subprocess.run(["bash", "-c", script], cwd=cwd, env=full,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return r.returncode, r.stdout


class DockerExecutor(Executor):
    """Corre los steps dentro del contenedor agente (docker exec)."""

    def __init__(self, container: str) -> None:
        self.container = container

    def run(self, script: str, cwd: Path, env: dict[str, str]) -> tuple[int, str]:
        cwd.mkdir(parents=True, exist_ok=True)
        args = ["docker", "exec", "-i", "-w", str(cwd)]
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        args += [self.container, "bash", "-c", script]
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return r.returncode, r.stdout


@dataclass
class Sim:
    root: Path                      # directorio de trabajo del simulador
    executor: Executor
    sim_env: dict[str, str] = field(default_factory=dict)
    pypi_url: str = "http://localhost:8081/"
    echo: object = None
    max_runs: int = 30

    def __post_init__(self) -> None:
        self.origin = self.root / "origin.git"
        self.dev = self.root / "dev"
        self.work = self.root / "agent"
        self.build_id = 0
        self.history: list[RunResult] = []
        self.pr_counter = 0

    # ── git del desarrollador ────────────────────────────────────────────────
    def git(self, *args: str, cwd: Path | None = None) -> str:
        r = subprocess.run(["git", *args], cwd=cwd or self.dev, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout.strip()

    def init_repo(self) -> None:
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.dev)], check=True)
        self.git("config", "user.email", "dev@sim")
        self.git("config", "user.name", "dev")
        self.git("remote", "add", "origin", str(self.origin))

    def commit(self, files: dict[str, str], msg: str) -> str:
        for rel, content in files.items():
            p = self.dev / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def remote_tags(self) -> dict[str, str]:
        out = self.git("ls-remote", "--tags", str(self.origin))
        tags = {}
        for line in out.splitlines():
            sha, ref = line.split("\t")
            name = ref[len("refs/tags/"):]
            if name.endswith("^{}"):
                tags[name[:-3]] = sha  # commit del tag anotado
            else:
                tags.setdefault(name, sha)
        return tags

    # ── pipelines ────────────────────────────────────────────────────────────
    def _snapshot(self, commit: str) -> Path:
        """Checkout del commit en un directorio propio (YAML del commit que se construye)."""
        snap = self.root / "snapshots" / commit
        if not snap.exists():
            snap.mkdir(parents=True)
            subprocess.run(f"git -C {self.origin} archive {commit} | tar -x -C {snap}",
                           shell=True, check=True)
        return snap

    def pipelines(self, commit: str) -> dict[str, tuple[Path, dict]]:
        snap = self._snapshot(commit)
        found = {}
        for f in sorted((snap / "pipelines").rglob("*.yml")):
            rel = f.relative_to(snap)
            if "templates" in rel.parts:
                continue
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            found[str(rel)] = (f, doc)
        return found

    def _run_pipeline(self, rel: str, event: Event) -> RunResult:
        snap = self._snapshot(event.commit)
        compiled = Compiler(snap).compile_pipeline(snap / rel)
        self.build_id += 1
        runner = Runner(self.executor, self.origin, self.work, self.sim_env, self.pypi_url,
                        echo=self.echo or (lambda s: None))
        res = runner.run(compiled, rel, event, self.build_id)
        self.history.append(res)
        return res

    def _changed(self, before: str | None, after: str) -> list[str]:
        if before is None:
            return self.git("show", "--name-only", "--format=", after).splitlines()
        return self.git("diff", "--name-only", before, after).splitlines()

    def _process(self, queue: deque) -> list[RunResult]:
        runs: list[RunResult] = []
        while queue:
            ref, commit, before = queue.popleft()
            changed = [] if ref.startswith("refs/tags/") else self._changed(before, commit)
            for rel, (_f, doc) in self.pipelines(commit).items():
                if not ci_triggers(doc.get("trigger"), ref, changed):
                    continue
                if len(self.history) >= self.max_runs:
                    raise RuntimeError("demasiados runs encadenados — ¿loop de tags?")
                tags_before = self.remote_tags()
                res = self._run_pipeline(rel, Event(ref, commit))
                runs.append(res)
                for tag, sha in sorted(self.remote_tags().items()):
                    if tag not in tags_before:
                        queue.append((f"refs/tags/{tag}", sha, None))
        return runs

    def push(self, branch: str = "main") -> list[RunResult]:
        """git push de una rama + los runs que dispara (y los que disparen sus tags)."""
        before = None
        try:
            before = self.git("rev-parse", f"refs/remotes/origin/{branch}")
        except RuntimeError:
            pass
        self.git("push", "-q", "origin", f"{branch}:{branch}")
        self.git("fetch", "-q", "origin")
        after = self.git("rev-parse", branch)
        return self._process(deque([(f"refs/heads/{branch}", after, before)]))

    def push_tag(self, tag: str, ref: str = "HEAD") -> list[RunResult]:
        """Tag anotado hecho por una persona (release.sh) + los runs que dispara."""
        self.git("tag", "-a", tag, "-m", tag, ref)
        self.git("push", "-q", "origin", f"refs/tags/{tag}")
        sha = self.git("rev-parse", f"{tag}^{{commit}}")
        return self._process(deque([(f"refs/tags/{tag}", sha, None)]))

    def queue(self, pipeline: str, ref: str) -> list[RunResult]:
        """'Run pipeline' manual en Azure: corre `pipeline` sobre `ref` (rama o tag)
        aunque el trigger no matchee; los tags que empuje disparan sus runs."""
        sha = self.git("rev-parse", f"{ref}^{{commit}}")
        full = ref if ref.startswith("refs/") else (
            f"refs/tags/{ref}" if ref in self.remote_tags() else f"refs/heads/{ref}")
        tags_before = self.remote_tags()
        runs = [self._run_pipeline(pipeline, Event(full, sha, reason="Manual"))]
        new = deque((f"refs/tags/{t}", s, None) for t, s in sorted(self.remote_tags().items())
                    if t not in tags_before)
        return runs + self._process(new)

    def pull_request(self, source: str, target: str, pipelines: list[str]) -> list[RunResult]:
        """PR con Build validation: corre `pipelines` sobre el merge commit."""
        self.git("push", "-q", "origin", f"{source}:{source}")
        self.pr_counter += 1
        tmp = self.root / f"pr-{self.pr_counter}"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(tmp)], check=True)
        self.git("checkout", "-q", target, cwd=tmp)
        self.git("-c", "user.email=azdo@sim", "-c", "user.name=azdo", "merge", "-q", "--no-ff",
                 "-m", f"Merge PR {self.pr_counter}", f"origin/{source}", cwd=tmp)
        merge = self.git("rev-parse", "HEAD", cwd=tmp)
        ref = f"refs/pull/{self.pr_counter}/merge"
        self.git("push", "-q", "origin", f"HEAD:{ref}", cwd=tmp)
        runs = [self._run_pipeline(p, Event(ref, merge, reason="PullRequest")) for p in pipelines]
        return runs
