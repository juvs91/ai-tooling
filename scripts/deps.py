#!/usr/bin/env python3
# distributable: true
"""deps.py — qué carpetas del monorepo usa un servicio, leído de su manifiesto.

Ref: ADR-0080. Fuente única para release.sh (sparse checkout), ci-service.sh (hash
de imagen por contenido), ci-python.sh (validar trigger.paths) y el where-used.

La dependencia se declara por ruta en el manifiesto nativo de la carpeta:

  pyproject.toml  [tool.uv.sources] pkg = { path = "../../_shared/libs/x" }
                  [tool.uv.sources] pkg = { workspace = true }
  package.json    "pkg": "file:../../_shared/libs/x"   (o "link:...")
                  "pkg": "workspace:*"
  go.mod          replace mod => ../../_shared/libs/x
  deps.txt        una ruta por línea, relativa a la raíz del repo

Compatibilidad: si el manifiesto pide un paquete que existe en el repo pero no
declara su ruta (p. ej. "metalsa-auth>=0.1" sin [tool.uv.sources]), se toma por
nombre y se avisa con el bloque que falta. Con DEPS_STRICT=1 eso es error.

Lee de git (--ref, default HEAD), no del disco: funciona con sparse checkout y
partial clone, y el release puede preguntar por las dependencias del tag.

Uso:
  deps.py paths <dir>... [--ref R]        dir(s) + librerías, una por línea
  deps.py libs <dir>... [--ref R]         solo las librerías
  deps.py consumers <lib> [--ref R]       carpetas que dependen de <lib> (where-used)
  deps.py trigger-block <dir> [--ref R]   bloque paths.include para el trigger de Azure
  deps.py policy-filter <dir> [--ref R]   filtro de ruta para la policy de build validation
  deps.py audit [--ref R]                 dependencias tomadas por nombre, con el bloque que falta

Exit: 0 OK, 1 error, 2 uso incorrecto.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys

try:
    import tomllib
except ImportError:  # Python < 3.11: main() da el error con instrucciones
    tomllib = None

MANIFESTS = ("pyproject.toml", "package.json", "go.mod", "deps.txt")
NODE_DEP_KEYS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
GO_REPLACE = re.compile(r"=>\s*(\.{1,2}/\S*)")
GO_REPLACE_LEFT = re.compile(r"^\s*(?:replace\s+)?(\S+)(?:\s+v\S+)?\s*=>")
GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?([^\s()]+)\s+v\S+")
GO_MODULE = re.compile(r"^\s*module\s+(\S+)", re.M)
PY_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class DepsError(Exception):
    pass


def _norm_py(name: str) -> str:
    """Nombre de paquete Python normalizado (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _clean(path: str) -> str:
    path = posixpath.normpath(path.strip().removeprefix("./")).rstrip("/")
    if path in ("", "."):
        raise DepsError("la ruta no puede ser la raíz del repo")
    if path.startswith("../") or path == ".." or path.startswith("/"):
        raise DepsError(f"{path}: la ruta sale del repo")
    return path


class Repo:
    """Vista de solo lectura del árbol de git en un ref."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        self._files: list[str] | None = None
        self._fileset: set[str] = set()
        self._dirs: set[str] | None = None
        self._blobs: dict[str, str] = {}
        self._names: dict[tuple[str, str], str] | None = None
        # (manifiesto, paquete) → (carpeta, bloque que falta): deps tomadas por nombre
        self.implicit: dict[tuple[str, str], tuple[str, str]] = {}

    def _git(self, *args: str) -> str:
        r = subprocess.run(["git", *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise DepsError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    def files(self) -> list[str]:
        if self._files is None:
            out = self._git("ls-tree", "-r", "--name-only", "--full-tree", self.ref)
            self._files = out.splitlines()
            self._fileset = set(self._files)
        return self._files

    def dirs(self) -> set[str]:
        if self._dirs is None:
            self._dirs = set()
            for f in self.files():
                d = posixpath.dirname(f)
                while d and d not in self._dirs:
                    self._dirs.add(d)
                    d = posixpath.dirname(d)
        return self._dirs

    def read(self, path: str) -> str | None:
        if path not in self._blobs:
            self.files()
            if path not in self._fileset:
                return None
            self._blobs[path] = self._git("show", f"{self.ref}:{path}")
        return self._blobs[path]

    def require_dir(self, path: str, origin: str) -> None:
        if path not in self.dirs():
            raise DepsError(f"{origin}: la carpeta {path} no existe en {self.ref}")

    def find(self, kind: str, name: str) -> str | None:
        """Carpeta del paquete `name` (kind: py | node | go) definido en el repo, o None."""
        if self._names is None:
            self._names = {}
            for f in self.files():
                base, d = posixpath.basename(f), posixpath.dirname(f)
                if not d or base not in ("pyproject.toml", "package.json", "go.mod"):
                    continue
                try:  # un manifiesto roto en otra carpeta no tumba la búsqueda
                    if base == "pyproject.toml":
                        n = _load_toml(self, f).get("project", {}).get("name")
                        key = ("py", _norm_py(n)) if isinstance(n, str) else None
                    elif base == "package.json":
                        n = _load_json(self, f).get("name")
                        key = ("node", n) if isinstance(n, str) else None
                    else:
                        m = GO_MODULE.search(self.read(f) or "")
                        key = ("go", m.group(1)) if m else None
                except DepsError:
                    continue
                if key:
                    self._names.setdefault(key, d)
        return self._names.get((kind, _norm_py(name) if kind == "py" else name))

    def by_name(self, kind: str, name: str, origin: str) -> str:
        d = self.find(kind, name)
        if d is None:
            raise DepsError(f"{origin}: no hay ningún paquete '{name}' en el repo ({self.ref})")
        return d

    def implicit_dep(self, kind: str, name: str, d: str, f: str) -> str | None:
        """Paquete del repo pedido sin ruta: lo cuenta y registra el bloque que falta."""
        dep = self.find(kind, name)
        if dep is None or dep == d:
            return None
        rel = posixpath.relpath(dep, d)
        if kind == "py":
            snippet = f'[tool.uv.sources] {name} = {{ path = "{rel}" }}'
        elif kind == "node":
            snippet = f'"{name}": "file:{rel}"'
        else:
            snippet = f"replace {name} => {rel if rel.startswith('.') else './' + rel}"
        self.implicit[(f, name)] = (dep, snippet)
        return dep


def _load_toml(repo: Repo, path: str) -> dict:
    try:
        return tomllib.loads(repo.read(path) or "")
    except tomllib.TOMLDecodeError as e:
        raise DepsError(f"{path}: TOML inválido: {e}") from e


def _load_json(repo: Repo, path: str) -> dict:
    try:
        data = json.loads(repo.read(path) or "{}")
    except json.JSONDecodeError as e:
        raise DepsError(f"{path}: JSON inválido: {e}") from e
    return data if isinstance(data, dict) else {}


def _py_requirement_names(data: dict) -> list[str]:
    project = data.get("project", {})
    reqs = list(project.get("dependencies", []))
    for extra in (project.get("optional-dependencies") or {}).values():
        reqs += extra
    for group in (data.get("dependency-groups") or {}).values():
        reqs += [r for r in group if isinstance(r, str)]
    names = []
    for r in reqs:
        m = PY_REQ_NAME.match(r) if isinstance(r, str) else None
        if m:
            names.append(m.group(1))
    return names


def _from_pyproject(repo: Repo, d: str, f: str) -> list[str]:
    out = []
    data = _load_toml(repo, f)
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    declared = {_norm_py(k) for k in sources}
    for name in _py_requirement_names(data):
        if _norm_py(name) not in declared:
            dep = repo.implicit_dep("py", name, d, f)
            if dep:
                out.append(dep)
    for pkg, spec in sources.items():
        for s in spec if isinstance(spec, list) else [spec]:
            if not isinstance(s, dict):
                continue
            if "path" in s:
                out.append(_clean(posixpath.join(d, s["path"])))
            elif s.get("workspace"):
                out.append(repo.by_name("py", pkg, f))
    return out


def _from_package_json(repo: Repo, d: str, f: str) -> list[str]:
    out = []
    data = _load_json(repo, f)
    for key in NODE_DEP_KEYS:
        for pkg, spec in (data.get(key) or {}).items():
            if not isinstance(spec, str):
                continue
            if spec.startswith(("file:", "link:")):
                out.append(_clean(posixpath.join(d, spec.split(":", 1)[1])))
            elif spec.startswith("workspace:"):
                out.append(repo.by_name("node", pkg, f))
            else:
                dep = repo.implicit_dep("node", pkg, d, f)
                if dep:
                    out.append(dep)
    return out


def _from_go_mod(repo: Repo, d: str, f: str) -> list[str]:
    text = repo.read(f) or ""
    out = [_clean(posixpath.join(d, m.group(1))) for m in GO_REPLACE.finditer(text)]
    replaced, required, block = set(), [], ""
    for line in text.splitlines():
        line = line.split("//", 1)[0]
        head = line.strip()
        if head in ("require (", "replace ("):
            block = head.split()[0]
            continue
        if head == ")":
            block = ""
            continue
        if block == "replace" or head.startswith("replace "):
            m = GO_REPLACE_LEFT.match(line)
            if m:
                replaced.add(m.group(1))
        elif block == "require" or head.startswith("require "):
            m = GO_REQUIRE.match(line)
            if m:
                required.append(m.group(1))
    for mod in required:
        if mod not in replaced:
            dep = repo.implicit_dep("go", mod, d, f)
            if dep:
                out.append(dep)
    return out


def _from_deps_txt(repo: Repo, d: str, f: str) -> list[str]:
    out = []
    for line in (repo.read(f) or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(_clean(line))
    return out


READERS = {
    "pyproject.toml": _from_pyproject,
    "package.json": _from_package_json,
    "go.mod": _from_go_mod,
    "deps.txt": _from_deps_txt,
}


def direct(repo: Repo, d: str) -> set[str]:
    found: set[str] = set()
    for name, reader in READERS.items():
        f = f"{d}/{name}"
        if repo.read(f) is None:
            continue
        for dep in reader(repo, d, f):
            repo.require_dir(dep, f)
            if dep != d:
                found.add(dep)
    return found


def closure(repo: Repo, roots: list[str]) -> set[str]:
    """Todas las carpetas de las que dependen `roots`, siguiendo la cadena."""
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        d = stack.pop()
        for dep in direct(repo, d):
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen - set(roots)


def _roots(repo: Repo, dirs: list[str]) -> list[str]:
    roots = [_clean(d) for d in dirs]
    for d in roots:
        repo.require_dir(d, "argumento")
    return roots


def cmd_paths(repo: Repo, dirs: list[str]) -> list[str]:
    roots = _roots(repo, dirs)
    return sorted(set(roots) | closure(repo, roots))


def cmd_libs(repo: Repo, dirs: list[str]) -> list[str]:
    return sorted(closure(repo, _roots(repo, dirs)))


def cmd_consumers(repo: Repo, lib: str) -> list[str]:
    target = _roots(repo, [lib])[0]
    candidates = sorted({posixpath.dirname(f) for f in repo.files()
                         if posixpath.basename(f) in MANIFESTS and posixpath.dirname(f)})
    out = []
    for d in candidates:
        if d == target:
            continue
        try:
            if target in closure(repo, [d]):
                out.append(d)
        except DepsError as e:
            print(f"deps: aviso: {d} se omitió: {e}", file=sys.stderr)
    return out


def cmd_audit(repo: Repo) -> list[str]:
    for d in sorted({posixpath.dirname(f) for f in repo.files()
                     if posixpath.basename(f) in MANIFESTS and posixpath.dirname(f)}):
        try:
            direct(repo, d)
        except DepsError as e:
            print(f"deps: aviso: {d} se omitió: {e}", file=sys.stderr)
    if not repo.implicit:
        return ["sin dependencias tomadas por nombre: todas declaran su ruta"]
    out = []
    for (f, pkg), (dep, snippet) in sorted(repo.implicit.items()):
        out += [f"{f}: {pkg} → {dep}", f"    agrega: {snippet}"]
    return out


def cmd_trigger_block(repo: Repo, d: str) -> list[str]:
    return ["  paths:", "    include:"] + [f"      - {p}" for p in cmd_paths(repo, [d])]


def cmd_policy_filter(repo: Repo, d: str) -> list[str]:
    return ["; ".join(f"/{p}/*" for p in cmd_paths(repo, [d]))]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="deps.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, nargs in (("paths", "+"), ("libs", "+"), ("consumers", 1),
                        ("trigger-block", 1), ("policy-filter", 1), ("audit", 0)):
        p = sub.add_parser(name)
        if nargs:
            p.add_argument("dirs", nargs=nargs)
        p.add_argument("--ref", default="HEAD")
    args = ap.parse_args(argv)

    if tomllib is None:
        print("deps: requiere Python >= 3.11 (tomllib); "
              f"este es {sys.version.split()[0]}", file=sys.stderr)
        return 1

    repo = Repo(args.ref)
    handlers = {
        "paths": lambda: cmd_paths(repo, args.dirs),
        "libs": lambda: cmd_libs(repo, args.dirs),
        "consumers": lambda: cmd_consumers(repo, args.dirs[0]),
        "trigger-block": lambda: cmd_trigger_block(repo, args.dirs[0]),
        "policy-filter": lambda: cmd_policy_filter(repo, args.dirs[0]),
        "audit": lambda: cmd_audit(repo),
    }
    try:
        lines = handlers[args.cmd]()
    except DepsError as e:
        print(f"deps: {e}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    if args.cmd != "audit":  # audit ya los listó en stdout
        for (f, pkg), (dep, snippet) in sorted(repo.implicit.items()):
            print(f"deps: aviso: {f}: '{pkg}' se tomó por nombre ({dep}); declara la ruta: {snippet}",
                  file=sys.stderr)
    if repo.implicit and os.environ.get("DEPS_STRICT") == "1":
        print("deps: DEPS_STRICT=1: hay dependencias sin ruta declarada (ver arriba)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
