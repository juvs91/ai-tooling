"""
check_adr_gate.py — ADR-first gate enforcer para ai-tooling.

Verifica que cualquier cambio a rutas guardadas (vendor/ proxy core, .agents/skills/)
venga acompañado de un nuevo ADR en docs/adr/.

Exit codes:
  0  — gate pasado (no hay archivos guardados, o ADR presente, o skip flag)
  1  — gate fallido (archivos guardados cambiados sin ADR)

Uso (CI / pre-commit):
  python tools/check_adr_gate.py \\
      --changed-files "vendor/claude-code-proxy/server.py" \\
      --new-files     "docs/adr/ADR-0002-new-decision.md" \\
      --commit-message "feat: add transformer"
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas guardadas — editar aquí para ajustar a tu proyecto
# ---------------------------------------------------------------------------

GUARDED_PATTERNS = [
    "vendor/claude-code-proxy/*.py",
    "vendor/claude-code-proxy/**/*.py",
    ".agents/skills/**/*.md",
]

EXCLUSION_PATTERNS = [
    "*/__pycache__/*",
    "*.pyc",
    "*/tests/*",
    "*/test_*.py",
]

ADR_PATTERN = "docs/adr/ADR-*.md"
SKIP_TOKEN = "[skip-adr]"
BYPASS_LOG = ".adr-gate-bypasses.log"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(path: str) -> str:
    """Forward-slash, sin ./ ni / al inicio. Rechaza traversal."""
    p = path.strip().replace("\\", "/")
    if p.startswith("/") or ".." in p.split("/"):
        return ""
    return p.lstrip("./")


def _split_file_list(raw: str) -> list[str]:
    parts = []
    for token in raw.replace("\n", " ").split(" "):
        token = token.strip()
        if token:
            norm = _normalise(token)
            if norm:
                parts.append(norm)
    return parts


def _is_excluded(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in EXCLUSION_PATTERNS)


def _is_guarded(path: str) -> bool:
    if _is_excluded(path):
        return False
    return any(fnmatch.fnmatch(path, pat) for pat in GUARDED_PATTERNS)


def _is_new_adr(path: str) -> bool:
    return fnmatch.fnmatch(path, ADR_PATTERN)


def _record_bypass(reason: str, guarded: list[str], msg: str) -> None:
    ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    files_snippet = ", ".join(guarded[:5])
    if len(guarded) > 5:
        files_snippet += f" … (+{len(guarded) - 5} more)"
    line = f"{ts} | reason={reason} | files=[{files_snippet}] | msg={msg[:120]!r}\n"
    try:
        with Path(BYPASS_LOG).open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Core gate logic
# ---------------------------------------------------------------------------

def run_gate(
    changed_files: list[str],
    new_files: list[str],
    commit_message: str,
    skip_flag: bool,
) -> int:
    print("=" * 60)
    print("ADR GATE — verificando cambios a rutas guardadas")
    print("=" * 60)

    guarded_changed = [f for f in changed_files if _is_guarded(f)]

    print(f"\nArchivos revisados  : {len(changed_files)}")
    print(f"Archivos guardados  : {len(guarded_changed)}")

    if not guarded_changed:
        print("\n[PASS] Sin cambios a rutas guardadas. Gate abierto.")
        return 0

    print("\nArchivos guardados modificados:")
    for f in guarded_changed:
        print(f"  - {f}")

    if skip_flag:
        print("\n[WARN] Flag --skip-adr detectado. Gate bypassed.")
        _record_bypass("--skip-adr flag", guarded_changed, commit_message)
        return 0

    if SKIP_TOKEN in (commit_message or ""):
        print(f"\n[WARN] '{SKIP_TOKEN}' en el commit message. Gate bypassed.")
        _record_bypass(SKIP_TOKEN, guarded_changed, commit_message)
        return 0

    new_adrs = [f for f in new_files if _is_new_adr(f)]

    print(f"\nNuevos ADRs en este commit: {len(new_adrs)}")
    for f in new_adrs:
        print(f"  - {f}")

    if new_adrs:
        print("\n[PASS] Cambios acompañados de nuevo ADR. Gate abierto.")
        return 0

    changed_list = "\n".join(f"  - {f}" for f in guarded_changed)
    print(f"""
ADR-GATE FALLÓ: se modificaron rutas guardadas sin un nuevo ADR.

Archivos modificados:
{changed_list}

Pasos requeridos:
  1. Lee .agents/skills/software/architecture/architect/SKILL.md
  2. Diseña la decisión y trade-offs
  3. Lee .agents/skills/software/architecture/adr-writer/SKILL.md
  4. Crea docs/adr/ADR-NNNN-<titulo>.md
  5. Haz commit del ADR junto con el código

Arreglo trivial: agrega [skip-adr] al commit message.
""".strip())
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="check_adr_gate",
        description="Enforce ADR-first: cambios a proxy/skills requieren ADR.",
    )
    parser.add_argument("--changed-files", metavar="FILES", default="")
    parser.add_argument("--new-files", metavar="FILES", default="")
    parser.add_argument("--commit-message", metavar="MSG", default="")
    parser.add_argument("--skip-adr", action="store_true", default=False)
    args = parser.parse_args()

    if args.changed_files.strip():
        changed_files = _split_file_list(args.changed_files)
    elif not sys.stdin.isatty():
        changed_files = _split_file_list(sys.stdin.read())
    else:
        changed_files = []

    new_files = _split_file_list(args.new_files) if args.new_files.strip() else []

    sys.exit(run_gate(
        changed_files=changed_files,
        new_files=new_files,
        commit_message=args.commit_message,
        skip_flag=args.skip_adr,
    ))


if __name__ == "__main__":
    main()
