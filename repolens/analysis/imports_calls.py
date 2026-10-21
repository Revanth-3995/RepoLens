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
    # qualified name -> names bound by a direct nested def/class and never
    # rebound, imported, or caught elsewhere in the same scope. These can be
    # resolved to their nested symbol without guessing.
    stable_local_defs: dict[str, set[str]]


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


def _collect_local_info(func: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[set[str], set[str]]:
    """Names bound directly inside a function and its stable nested definitions.

    Conservative scope tracking: params are included (they shadow module-level
    definitions); nested definitions only bind their own name. Names declared
    ``global``/``nonlocal`` are not local to this function.

    A nested function/class name is **stable** when it is not also a parameter,
    imported, assigned, deleted, or bound by an ``except ... as`` target anywhere
    in the same scope. Only stable nested definitions are candidates for static
    call resolution.
    """
    declared_non_local: set[str] = set()

    def collect_declarations(node: ast.AST) -> None:
        if isinstance(node, ast.Global | ast.Nonlocal):
            declared_non_local.update(node.names)
            return
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            return
        for child in ast.iter_child_nodes(node):
            collect_declarations(child)

    for stmt in func.body:
        collect_declarations(stmt)

    params: set[str] = set()
    args = func.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        params.add(arg.arg)
    if args.vararg:
        params.add(args.vararg.arg)
    if args.kwarg:
        params.add(args.kwarg.arg)

    bound: set[str] = set(params)
    nested_defs: set[str] = set()
    rebound: set[str] = set(params)

    def add(name: str, *, nested: bool) -> None:
        if name in declared_non_local:
            return
        bound.add(name)
        if nested:
            nested_defs.add(name)
        else:
            rebound.add(name)

    def handle(node: ast.AST) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            add(node.name, nested=True)
            # do not descend into nested function bodies
            return
        if isinstance(node, ast.ClassDef):
            add(node.name, nested=True)
            return
        if isinstance(node, ast.Lambda):
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            add(node.id, nested=False)
        if isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
            add(node.name, nested=False)
        if isinstance(node, ast.MatchMapping) and node.rest:
            add(node.rest, nested=False)
        if isinstance(node, ast.ExceptHandler):
            if node.name:
                add(node.name, nested=False)
        elif isinstance(node, ast.alias):
            add(node.asname or node.name.split(".")[0], nested=False)
        for child in ast.iter_child_nodes(node):
            handle(child)

    for stmt in func.body:
        handle(stmt)
    stable = {name for name in nested_defs if name not in rebound}
    return bound, stable


class _ExtractionVisitor:
    def __init__(self, node_to_qualified: dict[int, str], module_qualified: str) -> None:
        self._node_to_qualified = node_to_qualified
        self._module_qualified = module_qualified
        self.imports: list[ImportRecord] = []
        self.calls: list[CallRecord] = []
        self.has_star_import = False
        self.local_names: dict[str, set[str]] = {}
        self.stable_local_defs: dict[str, set[str]] = {}
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
                    bound, stable = _collect_local_info(node)
                    self.local_names[qual] = bound
                    self.stable_local_defs[qual] = stable
        return ExtractionResult(
            imports=self.imports,
            calls=self.calls,
            has_star_import=self.has_star_import,
            local_names=self.local_names,
            stable_local_defs=self.stable_local_defs,
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
