"""
check_adr_sections.py — Verifica que cada ADR bajo docs/adr/ tenga las
secciones obligatorias: Status, Date, Context, Decision.

Uso agéntico: este script existe para que agentes (humanos o LLM) detecten
ADRs incompletos antes de tratarlos como fuente de verdad de una decisión
arquitectónica. Un ADR sin `Status` no permite saber si la decisión sigue
vigente; sin `Date` no se puede ubicar en el tiempo; sin `Context`/`Decision`
no documenta ni el problema ni la resolución — deja de ser un ADR y pasa a
ser una nota suelta.

Qué hace exactamente:
  1. Busca archivos `--pattern` (default: `ADR-*.md`) directamente bajo
     `--root` (default: `docs/adr`), sin recursión — los ADR de este repo
     viven todos en un único directorio plano.
  2. Para cada archivo y cada sección requerida (`--sections`, default:
     `Status,Date,Context,Decision`), busca dos formas de sección
     igualmente válidas, porque el corpus real de ADRs de este repo mezcla
     ambas (ver docs/adr/ADR-0001, ADR-0004, ADR-0010, ADR-0022):
       a) **Heading style**: una línea `#`..`######` cuyo texto empieza con
          el nombre de la sección (con límite de palabra), ej. `## Context`,
          `## Decision Outcome`, `## Context and Problem Statement`.
       b) **Inline field style**: una línea (opcionalmente con viñeta `-`/`*`
          y/o envuelta en `**negrita**`, en cualquier combinación) cuyo
          texto — tras quitar viñeta y asteriscos — empieza con el nombre de
          la sección seguido de `:`, ej. `- **Date**: 2026-03-22`,
          `**Status:** Accepted`.
     Cualquiera de las dos formas cuenta como "sección presente". Esto es
     deliberado: unificar el formato de ~46 ADRs existentes está fuera del
     alcance de este checker — su trabajo es reportar, no reescribir.
  3. Con `--i18n-es`, además acepta el equivalente en español de cada
     sección (Status→Estado, Date→Fecha, Context→Contexto,
     Decision→Decisión/Decision) usando las mismas dos formas. Sin este
     flag (default), un ADR que solo tenga `## Contexto`/`## Decisión` se
     reporta como si le faltaran `Context`/`Decision` — el usuario pidió
     verificar esos nombres en inglés explícitamente; el flag existe para
     cuando se quiera un chequeo más laxo sobre el mismo corpus mixto.
  4. Reporta, por archivo, la lista exacta de secciones requeridas que NO
     se encontraron (ninguna de las dos formas, en ningún alias habilitado).

Cómo debe interpretar un agente el resultado:
  - Exit code 0: todos los ADR encontrados tienen las secciones requeridas.
  - Exit code 1: al menos un ADR es inválido. La salida (stdout) lista cada
    archivo problemático con las secciones faltantes — el agente debe abrir
    ESE archivo y agregar la sección faltante (heading o field style, a
    elección de quien edite).
  - Exit code 2: error de uso (`--root` no existe o no es un directorio).
  - Con `--json`: mismo resultado como un único objeto JSON a stdout, sin
    texto mezclado — mismo contrato que `tools/check_skill_frontmatter.py`.
  - Si no se encuentra ningún archivo bajo `--root` que matchee `--pattern`:
    se reporta como éxito (0 revisados, 0 problemas), igual que los demás
    checkers de este repo — "cero encontrados" puede ser legítimo y es
    responsabilidad del caller decidir si eso es sospechoso.

No requiere dependencias externas — solo stdlib (`re`, `argparse`, `json`,
`pathlib`). No modifica ningún archivo: solo reporta.

Exit codes:
  0 — todos los ADR encontrados tienen las secciones requeridas
  1 — al menos un ADR le falta alguna sección requerida
  2 — error de uso (--root inexistente, etc.)

Uso (CI / pre-commit / manual):
  python tools/check_adr_sections.py
  python tools/check_adr_sections.py --root docs/adr --sections Status,Date,Context,Decision
  python tools/check_adr_sections.py --i18n-es
  python tools/check_adr_sections.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = "docs/adr"
DEFAULT_PATTERN = "ADR-*.md"
DEFAULT_SECTIONS = ("Status", "Date", "Context", "Decision")

# Alias en español por sección canónica (solo se usan con --i18n-es).
_ES_ALIASES: dict[str, tuple[str, ...]] = {
    "status": ("estado",),
    "date": ("fecha",),
    "context": ("contexto",),
    "decision": ("decisión", "decision"),
}

_HEADING_RE = re.compile(r"^#{1,6}\s*(\S.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+")


@dataclass(frozen=True)
class Finding:
    """Un ADR incompleto: ruta (str, tal como se descubrió) y la tupla de
    nombres de sección requeridos que no se encontraron en ninguna forma."""

    path: str
    missing_sections: tuple[str, ...]

    @property
    def reason(self) -> str:
        return f"faltan secciones: {', '.join(self.missing_sections)}"


def _aliases_for(section: str, i18n_es: bool) -> tuple[str, ...]:
    canonical = section.strip()
    if not i18n_es:
        return (canonical,)
    return (canonical, *_ES_ALIASES.get(canonical.lower(), ()))


def _section_present(text: str, aliases: tuple[str, ...]) -> bool:
    """True si alguna línea de `text` satisface heading-style o
    inline-field-style para cualquiera de los `aliases` dados (ver
    docstring del módulo, punto 2, para el detalle exacto de cada forma)."""
    patterns = [re.compile(re.escape(a), re.IGNORECASE) for a in aliases]

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            heading_text = heading_match.group(1)
            for alias, pat in zip(aliases, patterns):
                if re.match(rf"^{pat.pattern}\b", heading_text, re.IGNORECASE):
                    return True
            continue  # una línea heading no es también field-style

        no_bullet = _BULLET_RE.sub("", stripped)
        normalized = no_bullet.replace("*", "").strip()
        for alias in aliases:
            if re.match(rf"^{re.escape(alias)}\s*:", normalized, re.IGNORECASE):
                return True

    return False


def check_file(path: Path, sections: tuple[str, ...], i18n_es: bool) -> Finding | None:
    """Evalúa un único ADR. Retorna None si tiene todas las secciones
    requeridas, o un Finding con las faltantes. Un error de lectura
    (permisos, encoding, etc.) se reporta como Finding con todas las
    secciones marcadas como faltantes, en vez de propagar la excepción —
    este script nunca debe crashear a mitad de un recorrido."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return Finding(str(path), sections)

    missing = tuple(
        section
        for section in sections
        if not _section_present(text, _aliases_for(section, i18n_es))
    )
    if missing:
        return Finding(str(path), missing)
    return None


def find_target_files(root: Path, pattern: str) -> list[Path]:
    return sorted(root.glob(pattern))


def run_check(
    root: Path, pattern: str, sections: tuple[str, ...], i18n_es: bool
) -> tuple[list[Finding], int]:
    """Retorna (findings, total_archivos_revisados). `findings` está vacío
    si todos los ADR son válidos (incluyendo el caso trivial de 0 archivos
    encontrados)."""
    files = find_target_files(root, pattern)
    findings = [
        f for f in (check_file(p, sections, i18n_es) for p in files) if f is not None
    ]
    return findings, len(files)


def _relpath(raw_path: str) -> str:
    try:
        return str(Path(raw_path).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return raw_path


def render_text(
    findings: list[Finding], total: int, root: str, sections: tuple[str, ...]
) -> str:
    lines = [
        "=" * 60,
        f"ADR SECTIONS CHECK — root={root} sections={','.join(sections)}",
        "=" * 60,
        "",
        f"ADRs revisados      : {total}",
        f"ADRs incompletos    : {len(findings)}",
    ]
    if not findings:
        lines.append("")
        lines.append(
            f"[PASS] Los {total} ADR(s) revisados tienen todas las secciones requeridas."
        )
        return "\n".join(lines)

    lines.append("")
    lines.append("ADRs con secciones faltantes:")
    for finding in findings:
        lines.append(f"  - {_relpath(finding.path)}: {finding.reason}")
    return "\n".join(lines)


def render_json(
    findings: list[Finding], total: int, root: str, sections: tuple[str, ...]
) -> str:
    payload = {
        "root": root,
        "sections": list(sections),
        "total_files": total,
        "ok": len(findings) == 0,
        "problem_count": len(findings),
        "problems": [
            {"path": _relpath(f.path), "missing_sections": list(f.missing_sections)}
            for f in findings
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="check_adr_sections",
        description=(
            "Verifica que cada ADR bajo --root tenga las secciones requeridas "
            "(--sections), detectadas en heading-style o inline-field-style. "
            "Ver el docstring del módulo para el contrato completo de exit "
            "codes y formato de salida — pensado para consumo agéntico (CI, "
            "hooks, pre-commit, revisión manual)."
        ),
    )
    parser.add_argument(
        "--root",
        default=DEFAULT_ROOT,
        metavar="PATH",
        help=f"Directorio a recorrer (sin recursión) buscando --pattern (default: {DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        metavar="GLOB",
        help=f"Patrón glob de archivos a revisar dentro de --root (default: {DEFAULT_PATTERN})",
    )
    parser.add_argument(
        "--sections",
        default=",".join(DEFAULT_SECTIONS),
        metavar="A,B,C",
        help=f"Lista separada por comas de secciones requeridas (default: {','.join(DEFAULT_SECTIONS)})",
    )
    parser.add_argument(
        "--i18n-es",
        action="store_true",
        default=False,
        help=(
            "Además del nombre en inglés, acepta el equivalente en español "
            "de cada sección (Estado/Fecha/Contexto/Decisión) como válido."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emite el resultado como un único objeto JSON a stdout, en vez de texto legible.",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: --root '{args.root}' no existe o no es un directorio", file=sys.stderr)
        sys.exit(2)

    sections = tuple(s.strip() for s in args.sections.split(",") if s.strip())
    if not sections:
        print("error: --sections no puede resolver a una lista vacía", file=sys.stderr)
        sys.exit(2)

    findings, total = run_check(root, args.pattern, sections, args.i18n_es)

    if args.json:
        print(render_json(findings, total, args.root, sections))
    else:
        print(render_text(findings, total, args.root, sections))

    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
