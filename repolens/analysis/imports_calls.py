"""Extraction of import statements and call expressions from a parsed module.

One AST pass per file using a parent map for owner attribution: each record
is tagged with the enclosing function/method's qualified name (or the module
qualified name when the statement sits at module or class level).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from repolens.core.models import CallForm, CallRecord, ImportRecord


@dataclass
class ExtractionResult:
    imports: list[ImportRecord]
    calls: list[CallRecord]
    has_star_import: bool
    # enclosing function/method qualified name -> locally bound names
    local_names: dict[str, set[str]]


def _callee_repr(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparsable>"


def _attribute_chain(node: ast.expr) -> tuple[str | None, tuple[str, ...]] | None:
    """Split ``a.b.c`` into (``a``, (``b``, ``c``)); None if not a chain from a Name."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.reverse()
    return current.id, tuple(parts)


def _collect_local_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names bound directly inside a function (excluding nested def bodies).

    Conservative scope tracking: params are included (they shadow module-level
    definitions); nested definitions only bind their own name.
    """
    bound: set[str] = set()
    args = func.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        bound.add(arg.arg)
    if args.vararg:
        bound.add(args.vararg.arg)
    if args.kwarg:
        bound.add(args.kwarg.arg)

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                if not isinstance(child, ast.Lambda):
                    bound.add(child.name)
                # do not descend into nested function bodies
                continue
            if isinstance(child, ast.ClassDef):
                bound.add(child.name)
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store | ast.Del):
                bound.add(child.id)
            # until we have proper AST typing; keep structure intact
            handler = getattr(ast, "except_handler", None)
            if handler and isinstance(child, handler):
                exc = child
                name = getattr(exc, "name", None)
                if name:
                    bound.add(name)
            elif isinstance(child, ast.alias):
                bound.add(child.asname or child.name.split(".")[0])
            elif isinstance(child, ast.NamedExpr):
                pass
            walk(child)

    for stmt in func.body:
        walk(stmt)
    return bound


class _ExtractionVisitor:
    def __init__(self, node_to_qualified: dict[int, str], module_qualified: str) -> None:
        self._node_to_qualified = node_to_qualified
        self._module_qualified = module_qualified
        self.imports: list[ImportRecord] = []
        self.calls: list[CallRecord] = []
        self.has_star_import = False
        self.local_names: dict[str, set[str]] = {}
        parents: dict[ast.AST, ast.AST] = {}
        # NOTE: tree is passed via visit(); parents are built there once.
        self._parents = parents

    def run(self, tree: ast.Module) -> ExtractionResult:
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self._parents[child] = node
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                self._import(node)
            elif isinstance(node, ast.Call):
                self._call(node)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                qual = self._node_to_qualified.get(id(node))
                if qual is not None:
                    self.local_names[qual] = _collect_local_names(node)
        return ExtractionResult(
            imports=self.imports,
            calls=self.calls,
            has_star_import=self.has_star_import,
            local_names=self.local_names,
        )

    def _owner(self, node: ast.AST) -> str | None:
        """Qualified name of the enclosing function/method, else the module."""
        current: ast.AST | None = self._parents.get(node)
        while current is not None:
            qualified = self._node_to_qualified.get(id(current))
            if qualified is not None:
                return qualified
            current = self._parents.get(current)
        return self._module_qualified

    def _import(self, node: ast.Import | ast.ImportFrom) -> None:
        owner = self._owner(node)
        line = getattr(node, "lineno", 0)
        col = getattr(node, "col_offset", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                self.imports.append(
                    ImportRecord(
                        kind="import",
                        module=alias.name,
                        level=0,
                        alias=alias.asname,
                        names=[],
                        line=line,
                        col=col,
                        owner_qualified=owner,
                    )
                )
            return
        # ast.ImportFrom
        star = any(alias.name == "*" for alias in node.names)
        if star:
            self.has_star_import = True
        names: list[tuple[str, str | None]] = [(alias.name, alias.asname) for alias in node.names]
        self.imports.append(
            ImportRecord(
                kind="from",
                module=node.module or "",
                level=node.level,
                alias=None,
                names=names,
                star=star,
                line=line,
                col=col,
                owner_qualified=owner,
            )
        )

    def _call(self, node: ast.Call) -> None:
        owner = self._owner(node)
        func = node.func
        line = getattr(node, "lineno", 0)
        col = getattr(node, "col_offset", 0)
        if isinstance(func, ast.Name):
            record = CallRecord(
                func_repr=func.id,
                form=CallForm.NAME,
                root_name=func.id,
                attrs=(),
                line=line,
                col=col,
                owner_qualified=owner,
            )
        elif isinstance(func, ast.Attribute):
            chain = _attribute_chain(func)
            if chain is not None:
                root, attrs = chain
            else:
                root, attrs = None, ()
            record = CallRecord(
                func_repr=_callee_repr(func),
                form=CallForm.ATTRIBUTE,
                root_name=root,
                attrs=attrs,
                line=line,
                col=col,
                owner_qualified=owner,
            )
        else:
            record = CallRecord(
                func_repr=_callee_repr(func),
                form=CallForm.INDIRECT,
                root_name=None,
                attrs=(),
                line=line,
                col=col,
                owner_qualified=owner,
            )
        self.calls.append(record)


def extract_imports_and_calls(
    tree: ast.Module, node_to_qualified: dict[int, str], module_qualified: str
) -> ExtractionResult:
    """Extract all imports and calls from a parsed module."""
    return _ExtractionVisitor(node_to_qualified, module_qualified).run(tree)
