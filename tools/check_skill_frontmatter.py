"""
check_skill_frontmatter.py — Verifica que cada SKILL.md tenga un bloque de
frontmatter YAML válido con un campo `name` no vacío.

Uso agéntico: este script existe para que agentes (humanos o LLM) detecten
SKILL.md rotos antes de que el sistema de skills (Skill tool / sync_skills.sh)
intente resolverlos. Un SKILL.md sin frontmatter, o sin `name`, no puede
registrarse — el Skill tool no sabe bajo qué nombre invocarlo, y
`sync_skills.sh` no puede catalogarlo correctamente.

Qué hace exactamente:
  1. Busca recursivamente todos los archivos `--filename` (default: SKILL.md)
     bajo `--root` (default: .agents/skills), a cualquier profundidad.
  2. Para cada archivo, determina si empieza con un bloque de frontmatter YAML:
     primera línea exactamente `---`, seguida en algún punto posterior por una
     línea de cierre exactamente `---`. Si la primera línea no es `---`, o si
     nunca aparece una línea de cierre `---`, el archivo se marca "sin
     frontmatter" — sin importar qué contenido tenga después.
  3. Dentro de ese bloque (las líneas ENTRE los dos delimitadores), busca una
     línea de nivel superior que empiece exactamente con `--field:` (default:
     `name:`), sin indentación — una línea indentada con el mismo nombre
     (ej. un `name:` anidado bajo otra clave) NO cuenta como el campo de nivel
     superior. Se acepta el valor entre comillas simples o dobles; se
     despojan antes de validar.
  4. Clasifica cada archivo problemático con una razón exacta y una sola de
     estas tres categorías (útiles para que un agente decida la acción
     correctiva sin tener que re-parsear el mensaje):
       - "sin frontmatter"
       - "frontmatter sin campo `<field>`"
       - "frontmatter con `<field>` vacío"

Cómo debe interpretar un agente el resultado:
  - Exit code 0: todos los archivos encontrados son válidos. No se requiere
    ninguna acción.
  - Exit code 1: al menos un archivo es inválido. La salida (stdout) lista
    cada archivo problemático con su ruta (relativa al cwd si es posible) y
    la razón exacta — el agente debe abrir ESE archivo y corregir el
    frontmatter (agregar el bloque completo, agregar la clave faltante, o
    darle un valor no vacío a la clave existente).
  - Exit code 2: error de uso (ej. `--root` no existe o no es un
    directorio). El mensaje va a stderr, no a stdout.
  - Con `--json`: el mismo resultado se emite como UN ÚNICO objeto JSON a
    stdout (sin ningún otro texto mezclado), pensado para que un agente lo
    parsee sin ambigüedad. Sin `--json`, la salida es texto plano legible
    por humanos, en el mismo estilo que `tools/check_adr_gate.py`.
  - Si no se encuentra ningún archivo bajo `--root` que matchee `--filename`:
    esto se reporta como éxito (0 archivos revisados, 0 problemas) — el
    script NO trata "cero archivos encontrados" como un error, ya que puede
    ser legítimo (ej. un repo que aún no tiene skills). Si se esperaba
    encontrar archivos y el conteo es 0, es responsabilidad del caller
    (agente o humano) notar `total_files: 0` en la salida y decidir si eso
    es en sí mismo sospechoso para su caso de uso.

No requiere dependencias externas (deliberadamente NO usa PyYAML). El parseo
de frontmatter es ingenuo por diseño: solo busca la clave pedida a nivel
superior, línea por línea. No valida el resto del YAML, no soporta
comentarios inline después del valor, y no distingue anidamiento salvo por
indentación (cualquier espacio inicial excluye la línea de ser "nivel
superior"). Esto es intencional — un parser YAML completo está fuera del
alcance de este checker; ver docstring de `_find_field_value` para el detalle
exacto del matching.

Exit codes:
  0 — todos los archivos encontrados tienen frontmatter válido con el campo
  1 — al menos un archivo es inválido
  2 — error de uso (--root inexistente, etc.)

Uso (CI / pre-commit / manual / otro proyecto):
  python tools/check_skill_frontmatter.py
  python tools/check_skill_frontmatter.py --root .agents/skills --field name
  python tools/check_skill_frontmatter.py --filename AGENT.md --field id
  python tools/check_skill_frontmatter.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = ".agents/skills"
DEFAULT_FILENAME = "SKILL.md"
DEFAULT_FIELD = "name"

REASON_NO_FRONTMATTER = "sin frontmatter"


def _reason_no_field(field: str) -> str:
    return f"frontmatter sin campo `{field}`"


def _reason_empty_field(field: str) -> str:
    return f"frontmatter con `{field}` vacío"


@dataclass(frozen=True)
class Finding:
    """Un archivo problemático: ruta (str, tal como se descubrió) y la razón
    exacta (una de REASON_NO_FRONTMATTER / _reason_no_field / _reason_empty_field)."""

    path: str
    reason: str


def _strip_quotes(value: str) -> str:
    """Despoja comillas simples o dobles que envuelven todo el valor (no
    comillas parciales/desbalanceadas — en ese caso se retorna tal cual,
    solo con whitespace externo recortado)."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def _extract_frontmatter_lines(text: str) -> list[str] | None:
    """Retorna las líneas ENTRE los dos delimitadores `---`, o None si el
    archivo no empieza con un bloque de frontmatter bien formado.

    Reglas exactas:
      - La primera línea (después de rstrip) debe ser exactamente '---'.
        Si no lo es, retorna None inmediatamente (no busca un '---' más
        adelante en el archivo — el frontmatter, si existe, debe ser lo
        primero).
      - Se busca, a partir de la segunda línea, la primera línea siguiente
        que sea exactamente '---'. Esa es la línea de cierre.
      - Si se llega al final del archivo sin encontrar una línea de cierre,
        el bloque nunca se cerró — se retorna None (frontmatter malformado
        cuenta como "sin frontmatter" para este checker).
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == "---":
            return lines[1:idx]
    return None


def _find_field_value(frontmatter_lines: list[str], field: str) -> str | None:
    """Busca `<field>:` a nivel superior (columna 0, sin indentación) dentro
    de las líneas de frontmatter. Retorna:
      - None si la clave no aparece en ningún lado a nivel superior.
      - "" (string vacío) si la clave existe pero su valor, tras despojar
        comillas y whitespace, queda vacío. Distinto de None a propósito —
        el caller necesita diferenciar "no existe" de "existe pero vacío".
      - El valor (sin comillas, sin whitespace externo) en cualquier otro caso.

    Nota de diseño: una línea indentada (ej. "  name: foo" anidada bajo otra
    clave) NUNCA matchea, incluso si el nombre del campo coincide — solo
    cuenta el nivel superior. Esto es deliberado: un `name:` genuino de
    frontmatter de skill siempre va sin indentación en los archivos reales
    de este repo (ver .agents/skills/*/*/SKILL.md)."""
    prefix = f"{field}:"
    for line in frontmatter_lines:
        if line.startswith(prefix):
            return _strip_quotes(line[len(prefix) :])
    return None


def check_file(path: Path, field: str) -> Finding | None:
    """Evalúa un único archivo. Retorna None si es válido, o un Finding con
    la razón exacta si no lo es. Un error de lectura (permisos, encoding,
    etc.) también se reporta como Finding en vez de propagar la excepción —
    este script nunca debe crashear a mitad de un recorrido por un solo
    archivo ilegible."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Finding(str(path), f"no se pudo leer el archivo ({exc})")

    frontmatter_lines = _extract_frontmatter_lines(text)
    if frontmatter_lines is None:
        return Finding(str(path), REASON_NO_FRONTMATTER)

    value = _find_field_value(frontmatter_lines, field)
    if value is None:
        return Finding(str(path), _reason_no_field(field))
    if value == "":
        return Finding(str(path), _reason_empty_field(field))
    return None


def find_target_files(root: Path, filename: str) -> list[Path]:
    return sorted(root.rglob(filename))


def run_check(root: Path, filename: str, field: str) -> tuple[list[Finding], int]:
    """Retorna (findings, total_archivos_revisados). `findings` está vacío
    si todos los archivos son válidos (incluyendo el caso trivial de 0
    archivos encontrados)."""
    files = find_target_files(root, filename)
    findings = [f for f in (check_file(p, field) for p in files) if f is not None]
    return findings, len(files)


def _relpath(raw_path: str) -> str:
    """Ruta relativa al cwd cuando es posible; si no (ej. cwd distinto del
    repo, o path fuera del árbol actual), retorna el path tal como se
    descubrió, sin fallar."""
    try:
        return str(Path(raw_path).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return raw_path


def render_text(findings: list[Finding], total: int, root: str, field: str) -> str:
    lines = [
        "=" * 60,
        f"SKILL FRONTMATTER CHECK — root={root} field={field}",
        "=" * 60,
        "",
        f"Archivos revisados : {total}",
        f"Archivos con error  : {len(findings)}",
    ]
    if not findings:
        lines.append("")
        lines.append(
            f"[PASS] Los {total} archivo(s) revisados tienen frontmatter válido con `{field}`."
        )
        return "\n".join(lines)

    lines.append("")
    lines.append("Archivos problemáticos:")
    for finding in findings:
        lines.append(f"  - {_relpath(finding.path)}: {finding.reason}")
    return "\n".join(lines)


def render_json(findings: list[Finding], total: int, root: str, field: str) -> str:
    payload = {
        "root": root,
        "field": field,
        "total_files": total,
        "ok": len(findings) == 0,
        "problem_count": len(findings),
        "problems": [{"path": _relpath(f.path), "reason": f.reason} for f in findings],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="check_skill_frontmatter",
        description=(
            "Verifica que cada SKILL.md bajo --root tenga un bloque de "
            "frontmatter YAML válido (delimitado por '---') con un campo "
            "--field no vacío. Ver el docstring del módulo para el contrato "
            "completo de exit codes y formato de salida — pensado para "
            "consumo agéntico (CI, hooks, pre-commit)."
        ),
    )
    parser.add_argument(
        "--root",
        default=DEFAULT_ROOT,
        metavar="PATH",
        help=f"Directorio raíz a recorrer recursivamente (default: {DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--filename",
        default=DEFAULT_FILENAME,
        metavar="NAME",
        help=f"Nombre de archivo a buscar recursivamente (default: {DEFAULT_FILENAME})",
    )
    parser.add_argument(
        "--field",
        default=DEFAULT_FIELD,
        metavar="FIELD",
        help=f"Campo de frontmatter que debe existir y no estar vacío (default: {DEFAULT_FIELD})",
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

    findings, total = run_check(root, args.filename, args.field)

    if args.json:
        print(render_json(findings, total, args.root, args.field))
    else:
        print(render_text(findings, total, args.root, args.field))

    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
