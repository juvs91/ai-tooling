"""Monorepo de ejemplo para el simulador: los pipelines y scripts REALES de
ai-tooling + un servicio (ventas/api) y una lib (_shared/libs/auth) mínimos que
pasan ruff y pytest.
"""

from __future__ import annotations

import shutil
from pathlib import Path

AI_TOOLING = Path(__file__).resolve().parents[3]
TEMPLATES = AI_TOOLING / "templates" / "gitops" / "azure-pipelines" / "pipelines"
SCRIPTS = AI_TOOLING / "scripts"
CI_SCRIPTS = ("ci-lib.sh", "ci-service.sh", "ci-python.sh", "ci-pylib.sh", "ci-image.sh", "ci-acr.sh",
              "ci_pyproject.py")

SERVICE = {
    "ventas/api/pyproject.toml": """\
[project]
name = "ventas-api"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = []

[dependency-groups]
dev = []
""",
    "ventas/api/ventas_api.py": '''\
"""Servicio de ejemplo del simulador."""


def total(precios: list[float]) -> float:
    return round(sum(precios), 2)
''',
    "ventas/api/test_ventas_api.py": '''\
from ventas_api import total


def test_total() -> None:
    assert total([1.10, 2.20]) == 3.3
''',
    "ventas/api/Dockerfile": """\
# Contexto = raíz del repo: copia el servicio y sus libs (ADR-0060 §Instalación)
FROM busybox:1.36
COPY _shared/libs/auth /app/libs/auth
COPY ventas/api /app/svc
CMD ["cat", "/app/svc/pyproject.toml"]
""",
    "ventas/web/package.json": '{"name": "ventas-web", "version": "1.0.0"}\n',
}

LIB = {
    "_shared/libs/auth/pyproject.toml": """\
[project]
name = "metalsa-auth"
version = "0.2.0"
requires-python = ">=3.12"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["metalsa_auth"]
""",
    "_shared/libs/auth/metalsa_auth/__init__.py": '''\
"""Lib compartida de ejemplo."""


def token(user: str) -> str:
    return f"tok-{user}"
''',
    "_shared/libs/auth/tests/test_auth.py": '''\
from metalsa_auth import token


def test_token() -> None:
    assert token("a") == "tok-a"
''',
}


def files() -> dict[str, str]:
    out: dict[str, str] = {**SERVICE, **LIB}
    for f in sorted(TEMPLATES.rglob("*.yml")):
        out[f"pipelines/{f.relative_to(TEMPLATES)}"] = f.read_text(encoding="utf-8")
    for name in CI_SCRIPTS:
        out[f"scripts/{name}"] = (SCRIPTS / name).read_text(encoding="utf-8")
    return out


def write(dest: Path) -> None:
    for rel, content in files().items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        if rel.startswith("scripts/"):
            p.chmod(0o755)


def reset(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
