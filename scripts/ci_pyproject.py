#!/usr/bin/env python3
# distributable: true
"""ci_pyproject.py — lectura/escritura mínima de pyproject.toml para pipelines.

Ref: ADR-0061. Lo usa scripts/ci-pylib.sh (antes era un heredoc dentro del YAML).

Comandos:
  set-version <pyproject.toml> <versión>   reescribe `version = "…"` (la primera)
  name <pyproject.toml>                    imprime [project].name

Exit: 0 OK, 1 error, 2 uso incorrecto.
"""

import re
import sys
import tomllib
from pathlib import Path

VERSION_RE = re.compile(r'(?m)^(version\s*=\s*)"[^"]*"')


def set_version(path: Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    new, n = VERSION_RE.subn(rf'\g<1>"{version}"', text, count=1)
    if n != 1:
        sys.exit(f"ci_pyproject: no encontré 'version = \"…\"' en {path}")
    path.write_text(new, encoding="utf-8")


def project_name(path: Path) -> str:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    try:
        return data["project"]["name"]
    except KeyError:
        sys.exit(f"ci_pyproject: {path} no tiene [project].name")


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "set-version":
        set_version(Path(argv[1]), argv[2])
        return 0
    if len(argv) == 2 and argv[0] == "name":
        print(project_name(Path(argv[1])))
        return 0
    print("uso: ci_pyproject.py set-version <pyproject.toml> <versión> | name <pyproject.toml>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
