"""Evaluador del subconjunto de expresiones de Azure Pipelines que usan los templates.

Cubre ${{ }} (compilación), $[ ] (runtime) y `condition:`. Cualquier función o
sintaxis fuera del subconjunto lanza ExprError — el simulador nunca "adivina".
Ref: https://learn.microsoft.com/azure/devops/pipelines/process/expressions
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


class ExprError(Exception):
    pass


_TOKEN = re.compile(
    r"""\s*(?:
        (?P<str>'(?:[^']|'')*')
      | (?P<num>-?\d+(?:\.\d+)?)
      | (?P<ident>[A-Za-z_][A-Za-z0-9_\-]*)
      | (?P<op>[(),.\[\]])
    )""",
    re.X,
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ExprError(f"token inesperado en {text!r} posición {pos}: {text[pos:pos + 20]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return out


@dataclass
class _Lit:
    value: Any


@dataclass
class _Ref:
    name: str


@dataclass
class _Prop:
    obj: Any
    key: Any  # nodo


@dataclass
class _Call:
    name: str
    args: list


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.toks = _tokenize(text)
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok is None or (value is not None and tok[1] != value):
            raise ExprError(f"se esperaba {value!r} en {self.text!r}")
        self.i += 1
        return tok

    def parse(self) -> Any:
        node = self.expr()
        if self.peek() is not None:
            raise ExprError(f"sobra texto en {self.text!r}: {self.peek()}")
        return node

    def expr(self) -> Any:
        kind, val = self.take()
        if kind == "str":
            node: Any = _Lit(val[1:-1].replace("''", "'"))
        elif kind == "num":
            node = _Lit(float(val) if "." in val else int(val))
        elif kind == "ident":
            low = val.lower()
            if self.peek() and self.peek()[1] == "(":
                self.take("(")
                args = []
                if self.peek() and self.peek()[1] != ")":
                    args.append(self.expr())
                    while self.peek() and self.peek()[1] == ",":
                        self.take(",")
                        args.append(self.expr())
                self.take(")")
                node = _Call(low, args)
            elif low in ("true", "false"):
                node = _Lit(low == "true")
            elif low == "null":
                node = _Lit(None)
            else:
                node = _Ref(val)
        else:
            raise ExprError(f"token inesperado {val!r} en {self.text!r}")
        while self.peek() and self.peek()[1] in (".", "["):
            if self.take()[1] == ".":
                node = _Prop(node, _Lit(self.take()[1]))
            else:
                key = self.expr()
                self.take("]")
                node = _Prop(node, key)
        return node


def parse(text: str) -> Any:
    return _Parser(text).parse()


# ── Semántica ────────────────────────────────────────────────────────────────

def to_str(v: Any) -> str:
    """Conversión a string de Azure: booleanos como True/False, null vacío."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (list, dict)):
        raise ExprError(f"no se puede convertir a string un {type(v).__name__}: {v!r}")
    return str(v)


def truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        # Azure: solo '' es falso — el string 'false' es VERDADERO (fuente clásica de bugs)
        return v != ""
    if isinstance(v, (list, dict)):
        return True
    return bool(v)


def _eq(a: Any, b: Any) -> bool:
    # Azure: coerciona al tipo del lado izquierdo; strings case-insensitive
    if isinstance(a, bool) or isinstance(b, bool):
        return to_str(a).lower() == to_str(b).lower()
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return a == b
    return to_str(a).lower() == to_str(b).lower()


class Context:
    """Resolución de nombres + funciones de estado (succeeded(), …)."""

    def __init__(self, names: dict[str, Any] | None = None,
                 status: dict[str, Callable[[], bool]] | None = None) -> None:
        self.names = names or {}
        self.status = status or {}

    def lookup(self, name: str) -> Any:
        for k, v in self.names.items():
            if k.lower() == name.lower():
                return v
        raise ExprError(f"nombre desconocido en la expresión: {name}")


def _get(obj: Any, key: Any) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for k, v in obj.items():
            if isinstance(k, str) and isinstance(key, str) and k.lower() == key.lower():
                return v
        return None  # Azure: propiedad inexistente → null
    if hasattr(obj, "get_prop"):
        return obj.get_prop(key)
    if obj is None:
        return None
    raise ExprError(f"no se puede indexar {type(obj).__name__} con {key!r}")


def evaluate(node: Any, ctx: Context) -> Any:
    if isinstance(node, _Lit):
        return node.value
    if isinstance(node, _Ref):
        return ctx.lookup(node.name)
    if isinstance(node, _Prop):
        return _get(evaluate(node.obj, ctx), evaluate(node.key, ctx))
    if isinstance(node, _Call):
        return _call(node, ctx)
    raise ExprError(f"nodo desconocido {node!r}")


def _call(node: _Call, ctx: Context) -> Any:
    n, raw = node.name, node.args
    if n in ctx.status:
        if raw:
            raise ExprError(f"{n}() con argumentos no está soportado por el simulador")
        return ctx.status[n]()
    if n == "and":
        return all(truthy(evaluate(a, ctx)) for a in raw)
    if n == "or":
        return any(truthy(evaluate(a, ctx)) for a in raw)
    args = [evaluate(a, ctx) for a in raw]
    if n == "not":
        return not truthy(args[0])
    if n == "eq":
        return _eq(args[0], args[1])
    if n == "ne":
        return not _eq(args[0], args[1])
    if n == "in":
        return any(_eq(args[0], x) for x in args[1:])
    if n == "notin":
        return not any(_eq(args[0], x) for x in args[1:])
    if n == "startswith":
        return to_str(args[0]).lower().startswith(to_str(args[1]).lower())
    if n == "endswith":
        return to_str(args[0]).lower().endswith(to_str(args[1]).lower())
    if n == "contains":
        return to_str(args[1]).lower() in to_str(args[0]).lower()
    if n == "join":
        seq = args[1]
        if seq is None:
            return ""
        if not isinstance(seq, list):
            return to_str(seq)
        return to_str(args[0]).join(to_str(x) for x in seq)
    if n == "length":
        v = args[0]
        return 0 if v is None else len(v)
    if n == "coalesce":
        return next((a for a in args if a not in (None, "")), None)
    if n == "lower":
        return to_str(args[0]).lower()
    if n == "upper":
        return to_str(args[0]).upper()
    if n == "format":
        fmt = to_str(args[0])
        return re.sub(r"\{(\d+)\}", lambda m: to_str(args[int(m.group(1)) + 1]), fmt)
    raise ExprError(f"función no soportada por el simulador: {n}()")


def eval_text(text: str, ctx: Context) -> Any:
    return evaluate(parse(text), ctx)
