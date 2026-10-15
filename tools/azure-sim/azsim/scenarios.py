"""Escenarios end-to-end del simulador (Docker): el flujo de ADR-0060 completo,
con aserciones contra el registry, los locks y el feed REALES del stack.

Uso (normalmente vía tools/azure-sim/sim):
  python3 -m azsim.scenarios --work <dir> [escenario ...]

Los escenarios forman una historia (cada uno parte del estado del anterior):
  pr → merge → rerun-ci → rc-prod → hotfix → immutable → lib → auth-error → paths-drift
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import fixture
from .runtime import RunResult
from .sim import DockerExecutor, Sim

ACR = "http://localhost:5000"
MOCK = "http://localhost:8082"
PYPI = "http://localhost:8081"
SVC_CI = "pipelines/services/example-ventas-api-ci.yml"
SVC_REL = "pipelines/services/example-ventas-api-release.yml"
LIB_REL = "pipelines/libs/example-auth-release.yml"
REPO = "ventas/api"
ACCEPT = ("application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, "
          "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json")


class ScenarioFailed(AssertionError):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise ScenarioFailed(msg)
    print(f"   ✔ {msg}")


# ── estado real del stack ─────────────────────────────────────────────────────

def _get(url: str, method: str = "GET", data: dict | None = None, headers: dict | None = None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if body:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=30)


def tags(repo: str = REPO) -> list[str]:
    try:
        with _get(f"{ACR}/v2/{repo}/tags/list") as r:
            return sorted(json.load(r).get("tags") or [])
    except urllib.error.HTTPError:
        return []


def digest(tag: str, repo: str = REPO) -> str | None:
    try:
        with _get(f"{ACR}/v2/{repo}/manifests/{tag}", "HEAD", headers={"Accept": ACCEPT}) as r:
            return r.headers["Docker-Content-Digest"]
    except urllib.error.HTTPError:
        return None


def locks() -> dict:
    with _get(f"{MOCK}/_sim/acr/locks") as r:
        return json.load(r)


def view_calls() -> list[dict]:
    with _get(f"{MOCK}/_sim/artifacts/calls") as r:
        return json.load(r)


def fail_mode(mode: str) -> None:
    with _get(f"{MOCK}/_sim/acr/fail", "POST", {"mode": mode}):
        pass


def pypi_files(pkg: str) -> str:
    try:
        with _get(f"{PYPI}/simple/{pkg}/") as r:
            return r.read().decode()
    except urllib.error.HTTPError:
        return ""


def ctx_tags() -> list[str]:
    return [t for t in tags() if t.startswith("ctx-")]


def by(runs: list[RunResult], pipeline: str) -> list[RunResult]:
    return [r for r in runs if r.pipeline == pipeline]


def step_out(run: RunResult, stage: str, job: str, display: str) -> str:
    return run.step(stage, job, display).output


# ── escenarios ────────────────────────────────────────────────────────────────

def s_pr(sim: Sim) -> None:
    """PR con Build validation: tests + docker build sin push; nada llega al ACR."""
    sim.git("checkout", "-q", "-b", "feature/descuento")
    sim.commit({"ventas/api/ventas_api.py": fixture.SERVICE["ventas/api/ventas_api.py"]
                + '\n\ndef descuento(x: float) -> float:\n    return round(x * 0.9, 2)\n'}, "descuento")
    runs = sim.pull_request("feature/descuento", "main", [SVC_CI])
    sim.git("checkout", "-q", "main")
    run = runs[0]
    check(run.result == "Succeeded", "el run de validación del PR pasa")
    check(run.step("CI", "acr_build", "docker build (validación, sin push)").result == "Succeeded",
          "docker build de validación corrió (Dockerfile con contexto = raíz)")
    check(run.step("CI", "acr_build", "ensure-ctx → ACR").result == "Skipped", "ensure-ctx no corre en PR")
    check(tags() == [], "el ACR sigue vacío tras el PR")


def s_merge(sim: Sim) -> None:
    """Merge a main: ctx-<hash> se construye, se bloquea, -dev automático → dev."""
    sim.git("merge", "-q", "--no-ff", "-m", "Merge feature/descuento", "feature/descuento")
    runs = sim.push("main")
    ci, rel = by(runs, SVC_CI), by(runs, SVC_REL)
    check(len(ci) == 1 and ci[0].result == "Succeeded", "CI del servicio corre y pasa")
    check(len(rel) == 1 and rel[0].result == "Succeeded", "el tag -dev automático dispara el release")
    ctx = ctx_tags()
    check(len(ctx) == 1, f"un solo ctx en el ACR: {ctx}")
    dev = [t for t in tags() if "-dev." in t]
    check(len(dev) == 1, f"tag de imagen -dev publicado: {dev}")
    check(digest(dev[0]) == digest(ctx[0]), "X.Y.Z-dev.N apunta al MISMO digest que el ctx")
    lk = locks()
    check(lk.get(f"{REPO}:{ctx[0]}", {}).get("write") is False, "ctx bloqueado (tag)")
    check(lk.get(f"{REPO}@{digest(ctx[0])}", {}).get("write") is False, "ctx bloqueado (manifest)")
    check([e for e, _ in rel[0].deployments()] == ["ventas-api-dev"], "solo se desplegó a dev")
    check(rel[0].deployments()[0][1].endswith(digest(ctx[0])), "dev recibe la imagen por digest")


def s_rerun_ci(sim: Sim) -> None:
    """Re-run del CI con el mismo contenido: sin rebuild, nuevo -dev con el mismo digest."""
    sim.commit({"ventas/web/package.json": '{"name": "ventas-web", "version": "1.0.1"}\n'}, "web")
    runs = sim.push("main")
    check(by(runs, SVC_CI) == [], "un commit solo en ventas/web NO dispara el CI de ventas-api (paths)")
    before = ctx_tags()
    runs = sim.queue(SVC_CI, "main")
    out = step_out(runs[0], "CI", "acr_build", "ensure-ctx → ACR")
    check("sin rebuild" in out, "ensure-ctx detecta el ctx existente y no reconstruye")
    check(ctx_tags() == before, "no aparece un ctx nuevo")
    devs = sorted(t for t in tags() if "-dev." in t)
    check(len(devs) == 2 and digest(devs[0]) == digest(devs[1]), "el segundo -dev comparte digest con el primero")


def s_rc_prod(sim: Sim) -> None:
    """rc → qa; prod en HEAD de trunk tras commits ajenos → mismo digest que el rc."""
    rc = sim.push_tag("ventas-api@1.0.0-rc.1")
    check(rc[0].result == "Succeeded" and [e for e, _ in rc[0].deployments()] == ["ventas-api-qa"],
          "rc.1 se despliega solo a qa")
    sim.commit({"ventas/web/package.json": '{"name": "ventas-web", "version": "1.0.2"}\n'}, "web otra vez")
    sim.push("main")
    prod = sim.push_tag("ventas-api@1.0.0")
    run = prod[0]
    check(run.result == "Succeeded", "release de prod pasa")
    plan = step_out(run, "Resolve", "resolve", "Origen de la imagen")
    check("needs_build=false" in plan and "source=1.0.0-rc.1" in plan, "prod se promueve desde rc.1 sin build")
    check(digest("1.0.0") == digest("1.0.0-rc.1"), "prod recibe EXACTAMENTE el digest que aprobó QA")
    check([e for e, _ in run.deployments()] == ["ventas-api-prod"], "solo se desplegó a prod")


def s_immutable(sim: Sim) -> None:
    """Re-run idempotente; un tag publicado no se puede re-apuntar (ni con docker push)."""
    again = sim.queue(SVC_REL, "ventas-api@1.0.0")
    check(again[0].result == "Succeeded", "re-correr el release de prod es idempotente")
    agent = sim.executor
    code, out = agent.run(
        "docker pull -q busybox:1.36 >/dev/null && docker tag busybox:1.36 localhost:5000/ventas/api:1.0.0 "
        "&& docker push localhost:5000/ventas/api:1.0.0", sim.work, {})
    check(code != 0 and "write-disabled" in out,
          "push directo a un tag bloqueado es rechazado por el 'ACR' (405, lock del servidor)")
    env = {"ACR_LOGIN_SERVER": "localhost:5000"}
    origin_ctx = next(t for t in ctx_tags() if digest(t) == digest("1.0.0"))  # el hotfix agregó otro ctx
    code, out = agent.run(f"bash scripts/ci-acr.sh promote metalsaacr {REPO} {origin_ctx} 1.0.0", sim.dev, env)
    check(code == 0, "promote del mismo digest a un tag existente: OK (idempotente)")
    code, out = agent.run(f"bash scripts/ci-acr.sh promote metalsaacr {REPO} 1.0.0-hotfix.1 1.0.0", sim.dev, env)
    check(code != 0 and "inmutables" in out, "re-apuntar 1.0.0 a otro digest falla (tags inmutables)")
    check(digest("1.0.0") == digest("1.0.0-rc.1"), "1.0.0 sigue apuntando al digest aprobado")


def s_hotfix(sim: Sim) -> None:
    """hotfix/<svc>/* → CI → -hotfix.1 automático → release → prod."""
    sim.git("checkout", "-q", "-b", "hotfix/ventas-api/redondeo", "ventas-api@1.0.0")
    sim.commit({"ventas/api/ventas_api.py": fixture.SERVICE["ventas/api/ventas_api.py"].replace(
        "round(sum(precios), 2)", "round(sum(precios) + 0.0, 2)")}, "hotfix redondeo")
    runs = sim.push("hotfix/ventas-api/redondeo")
    sim.git("checkout", "-q", "main")
    ci = by(runs, SVC_CI)
    check(len(ci) == 1 and ci[0].result == "Succeeded", "CI corre en la rama hotfix")
    check(ci[0].step("CI", "acr_build", "Tag -hotfix automático").result == "Succeeded", "tag -hotfix automático")
    check("ventas-api@1.0.0-hotfix.1" in sim.remote_tags(), "tag git ventas-api@1.0.0-hotfix.1 creado")
    rel = by(runs, SVC_REL)
    check(len(rel) == 1 and [e for e, _ in rel[0].deployments()] == ["ventas-api-prod"], "hotfix va a prod")
    check(any("release.sh cherry" in m for _, m in ci[0].issues), "avisa del cherry obligatorio")
    check(digest("1.0.0-hotfix.1") not in (None, digest("1.0.0")), "hotfix es una imagen nueva (contenido distinto)")


def s_lib(sim: Sim) -> None:
    """lib auth: dev → @Local, rc → Prerelease, prod → Release; re-run → 409 omitido."""
    for tag, pep in (("auth@0.2.0-dev.1", "0.2.0.dev1"), ("auth@0.2.0-rc.1", "0.2.0rc1"), ("auth@0.2.0", "0.2.0")):
        runs = by(sim.push_tag(tag), LIB_REL)
        check(len(runs) == 1 and runs[0].result == "Succeeded", f"{tag} publica")
        check(f"-{pep}" in pypi_files("metalsa-auth"), f"{pep} está en el feed")
    calls = [c for c in view_calls() if c.get("view")]
    check([(c["version"], c["view"]) for c in calls] == [("0.2.0rc1", "Prerelease"), ("0.2.0", "Release")],
          "promociones a views: rc → Prerelease, prod → Release (dev se queda en @Local)")
    check(all(c["auth"] == "Bearer sim-access-token" for c in calls), "usa System.AccessToken mapeado por env:")
    rerun = sim.queue(LIB_REL, "auth@0.2.0")[0]
    check(rerun.result == "Succeeded" and any("ya estaba publicada" in m for _, m in rerun.issues),
          "re-run: 409 del feed se reporta como warning y no falla")


def s_auth_error(sim: Sim) -> None:
    """Error de auth en ACR: el CI falla y NO reconstruye ni publica."""
    for i, mode in enumerate(("auth", "list")):
        before = ctx_tags()
        fail_mode(mode)
        try:
            sim.commit({"ventas/api/ventas_api.py": fixture.SERVICE["ventas/api/ventas_api.py"]
                        + f'\n\nVERSION = "auth-error-{i}"\n'}, f"cambio con ACR fallando ({mode})")
            runs = by(sim.push("main"), SVC_CI)
        finally:
            fail_mode("")
        run = runs[0]
        check(run.result == "Failed", f"[{mode}] el CI falla con el error de autorización")
        out = step_out(run, "CI", "acr_build", "ensure-ctx → ACR")
        check("AuthorizationFailed" in out and "construyendo" not in out,
              f"[{mode}] el error NO se toma como 'no existe' (sin build)")
        check(ctx_tags() == before, f"[{mode}] no se publicó ningún ctx nuevo")
        check(run.step("CI", "acr_build", "Tag -dev automático").result == "Skipped", f"[{mode}] sin tag -dev")


def s_paths_drift(sim: Sim) -> None:
    """trigger.paths sin la lib → check-paths falla en el PR."""
    sim.git("checkout", "-q", "-b", "feature/drift", "main")
    ci = (sim.dev / SVC_CI).read_text().replace("      - _shared/libs/auth\n", "", 1)
    sim.commit({SVC_CI: ci}, "olvidé la lib en trigger.paths")
    run = sim.pull_request("feature/drift", "main", [SVC_CI])[0]
    sim.git("checkout", "-q", "main")
    check(run.result == "Failed", "el PR falla")
    check(run.step("CI", "python_ci", "Validar trigger.paths vs dependsOn").result == "Failed",
          "falla justo en check-paths")


SCENARIOS = {
    "pr": s_pr, "merge": s_merge, "rerun-ci": s_rerun_ci, "rc-prod": s_rc_prod, "hotfix": s_hotfix,
    "immutable": s_immutable, "lib": s_lib, "auth-error": s_auth_error, "paths-drift": s_paths_drift,
}


def _agent_container() -> str:
    out = subprocess.run(["docker", "compose", "-f", str(Path(__file__).parents[1] / "docker-compose.yml"),
                          "ps", "-q", "agent"], capture_output=True, text=True, check=True).stdout.strip()
    if not out:
        raise SystemExit("el agente no está corriendo — ejecuta: tools/azure-sim/sim up")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--quiet", action="store_true", help="no imprimir el log de cada run")
    ap.add_argument("names", nargs="*", default=list(SCENARIOS))
    a = ap.parse_args(argv)
    wanted = list(SCENARIOS) if a.names in ([], ["all"]) else a.names
    unknown = [n for n in wanted if n not in SCENARIOS]
    if unknown:
        print(f"escenarios desconocidos: {unknown}; disponibles: {list(SCENARIOS)}", file=sys.stderr)
        return 2
    # La historia es acumulativa: correr un escenario implica correr los anteriores.
    last = max(list(SCENARIOS).index(n) for n in wanted)
    plan = list(SCENARIOS)[: last + 1]

    fixture.reset(a.work)
    sim = Sim(a.work.resolve(), DockerExecutor(_agent_container()),
              echo=None if a.quiet else (lambda s: print(f"      {s}")))
    sim.init_repo()
    fixture.write(sim.dev)
    sim.commit({}, "monorepo inicial")
    sim.git("push", "-q", "origin", "main")

    failed = []
    for name in plan:
        print(f"\n▶ {name}: {SCENARIOS[name].__doc__.strip().splitlines()[0]}")
        try:
            SCENARIOS[name](sim)
        except Exception as e:  # noqa: BLE001 — se reporta y la historia se detiene
            print(f"   ✘ {type(e).__name__}: {e}")
            failed.append(name)
            break
    print("\n" + ("✅ todos los escenarios pasaron" if not failed else f"❌ falló: {failed[0]}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
