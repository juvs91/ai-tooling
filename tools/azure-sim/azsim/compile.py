"""Expansión de templates en tiempo de compilación (${{ }}), como hace Azure al
encolar un run: `template:` con rutas relativas, parámetros tipados con default,
${{ if }} / ${{ elseif }} / ${{ else }} / ${{ each }} e inserción de stepList.

Lo que no está en este subconjunto lanza CompileError en vez de ignorarse.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .expr import Context, ExprError, eval_text, to_str, truthy

EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
DIRECTIVE = re.compile(r"^\$\{\{\s*(if|elseif|else|each)\b(.*?)\}\}$", re.S)
EACH = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.+)$", re.S)
BODY_KEYS = ("stages", "jobs", "steps", "variables")
PARAM_TYPES = {"string", "boolean", "number", "object", "step", "stepList", "job", "jobList",
               "stage", "stageList", "deployment", "deploymentList"}


class CompileError(Exception):
    pass


def load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise CompileError(f"no existe el template {path}") from e


def _interp(text: str, names: dict[str, Any], where: str) -> Any:
    whole = EXPR.fullmatch(text.strip())
    try:
        if whole:
            return eval_text(whole.group(1), Context(names))
        return EXPR.sub(lambda m: to_str(eval_text(m.group(1), Context(names))), text)
    except ExprError as e:
        raise CompileError(f"{where}: {e}") from e


def _directive(key: Any) -> tuple[str, str] | None:
    if isinstance(key, str):
        m = DIRECTIVE.match(key.strip())
        if m:
            return m.group(1), m.group(2).strip()
    return None


class Compiler:
    def __init__(self, root: Path) -> None:
        self.root = root  # raíz del repo: los templates no pueden salir de ahí

    def compile_pipeline(self, path: Path) -> dict:
        doc = load_yaml(path)
        if not isinstance(doc, dict):
            raise CompileError(f"{path}: el pipeline no es un mapping")
        if "extends" in doc:
            raise CompileError(f"{path}: `extends` no está soportado por el simulador")
        params = self._bind(doc.get("parameters") or [], {}, path)
        names = {"parameters": params}
        out = {}
        for k, v in doc.items():
            if k in ("trigger", "pr", "parameters", "resources", "name"):
                out[k] = v
            else:
                out[k] = self.expand(v, names, path)
        return out

    # ── parámetros ───────────────────────────────────────────────────────────
    def _bind(self, declared: list, passed: dict, where: Path) -> dict:
        decl = {}
        for p in declared:
            if not isinstance(p, dict) or "name" not in p:
                raise CompileError(f"{where}: parámetro mal declarado {p!r}")
            decl[p["name"]] = p
        unknown = set(passed) - set(decl)
        if unknown:
            raise CompileError(f"{where}: parámetros no declarados: {sorted(unknown)}")
        bound = {}
        for name, p in decl.items():
            ptype = p.get("type", "string")
            if ptype not in PARAM_TYPES:
                raise CompileError(f"{where}: tipo de parámetro no soportado {ptype!r} ({name})")
            if name in passed:
                value = passed[name]
            elif "default" in p:
                value = p["default"]
            else:
                raise CompileError(f"{where}: falta el parámetro obligatorio {name!r}")
            bound[name] = self._coerce(value, ptype, name, where)
            if "values" in p and bound[name] not in p["values"]:
                raise CompileError(f"{where}: {name}={bound[name]!r} fuera de {p['values']}")
        return bound

    @staticmethod
    def _coerce(value: Any, ptype: str, name: str, where: Path) -> Any:
        if ptype == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            raise CompileError(f"{where}: {name} debe ser boolean, recibió {value!r}")
        if ptype == "string":
            if isinstance(value, (list, dict)):
                raise CompileError(f"{where}: {name} debe ser string, recibió {type(value).__name__}")
            return "" if value is None else to_str(value)
        if ptype == "number":
            return float(value) if "." in str(value) else int(value)
        if ptype.endswith("List"):
            if value is None:
                return []
            if not isinstance(value, list):
                raise CompileError(f"{where}: {name} debe ser {ptype}, recibió {type(value).__name__}")
        return value

    # ── expansión ────────────────────────────────────────────────────────────
    def expand(self, node: Any, names: dict, where: Path) -> Any:
        if isinstance(node, str):
            return _interp(node, names, str(where))
        if isinstance(node, list):
            return self._expand_list(node, names, where)
        if isinstance(node, dict):
            return self._expand_dict(node, names, where)
        return node

    def _branches(self, items: list[tuple[Any, Any]], names: dict, where: Path):
        """Recorre pares (clave, valor) y produce ('normal', k, v) o ('splice', v, names)."""
        matched_chain = None  # None = no hay cadena if abierta; True/False = ya se tomó una rama
        for key, value in items:
            d = _directive(key)
            if d is None:
                matched_chain = None
                yield ("normal", key, value, names)
                continue
            kind, arg = d
            if kind == "if":
                ok = truthy(_interp("${{ " + arg + " }}", names, str(where)))
                matched_chain = ok
                if ok:
                    yield ("splice", None, value, names)
            elif kind in ("elseif", "else"):
                if matched_chain is None:
                    raise CompileError(f"{where}: ${{{{ {kind} }}}} sin ${{{{ if }}}} previo")
                if matched_chain:
                    continue
                ok = True if kind == "else" else truthy(_interp("${{ " + arg + " }}", names, str(where)))
                if ok:
                    matched_chain = True
                    yield ("splice", None, value, names)
                if kind == "else":
                    matched_chain = None
            elif kind == "each":
                matched_chain = None
                m = EACH.match(arg)
                if not m:
                    raise CompileError(f"{where}: each mal formado: {arg!r}")
                var, coll = m.group(1), _interp("${{ " + m.group(2) + " }}", names, str(where))
                if isinstance(coll, dict):
                    seq = [{"key": k, "value": v} for k, v in coll.items()]
                elif isinstance(coll, list):
                    seq = coll
                elif coll is None:
                    seq = []
                else:
                    raise CompileError(f"{where}: each sobre un {type(coll).__name__}")
                for item in seq:
                    yield ("splice", None, value, {**names, var: item})

    def _expand_dict(self, node: dict, names: dict, where: Path) -> dict:
        out: dict = {}
        for kind, key, value, scope in self._branches(list(node.items()), names, where):
            if kind == "normal":
                out[self.expand(key, scope, where)] = self.expand(value, scope, where)
            else:
                expanded = self.expand(value, scope, where)
                if not isinstance(expanded, dict):
                    raise CompileError(f"{where}: rama condicional dentro de un mapping debe ser mapping")
                out.update(expanded)
        return out

    def _expand_list(self, node: list, names: dict, where: Path) -> list:
        out: list = []
        # Agrupar ítems directiva (dicts de una sola clave ${{ … }}) para encadenar if/else
        pairs: list[tuple[Any, Any]] = []
        for item in node:
            if isinstance(item, dict) and len(item) == 1 and _directive(next(iter(item))):
                pairs.append(next(iter(item.items())))
            else:
                pairs.append((None, item))
        for kind, _key, value, scope in self._branches(pairs, names, where):
            if kind == "normal":
                out.extend(self._expand_item(value, scope, where))
            else:
                expanded = self.expand(value, scope, where)
                out.extend(expanded if isinstance(expanded, list) else [expanded])
        return out

    def _expand_item(self, item: Any, names: dict, where: Path) -> list:
        if isinstance(item, dict) and isinstance(item.get("template"), str):
            return self._expand_template(item, names, where)
        if isinstance(item, str) and EXPR.fullmatch(item.strip()):
            v = _interp(item, names, str(where))
            return v if isinstance(v, list) else [v]  # inserción de stepList/jobList
        return [self.expand(item, names, where)]

    def _expand_template(self, use: dict, names: dict, where: Path) -> list:
        ref = self.expand(use["template"], names, where)
        if "@" in ref:
            raise CompileError(f"{where}: templates de otros repos ({ref}) no soportados")
        path = (where.parent / ref).resolve()
        if self.root.resolve() not in path.parents:
            raise CompileError(f"{where}: el template {ref} sale del repo")
        extra = set(use) - {"template", "parameters"}
        if extra:
            raise CompileError(f"{where}: claves no soportadas junto a template: {sorted(extra)}")
        doc = load_yaml(path)
        passed = self.expand(use.get("parameters") or {}, names, where)
        params = self._bind(doc.get("parameters") or [], passed, path)
        body = [k for k in BODY_KEYS if k in doc]
        if len(body) != 1:
            raise CompileError(f"{path}: el template debe tener exactamente una de {BODY_KEYS}")
        expanded = self.expand(doc[body[0]], {"parameters": params}, path)
        return expanded if isinstance(expanded, list) else [expanded]
