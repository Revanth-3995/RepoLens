"""Symbol extraction from Python ASTs.

Extracts module/class/function/method symbols including nested scopes, with
signatures, parameters, annotations, base classes, decorators, docstrings,
and one-based line ranges. Nothing is invented: absent information stays
``None``/empty. Identity (qualified name + ordinal) is computed per
docs/architecture.md section 4.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from repolens.core.models import ParameterInfo, SymbolDraft, SymbolKind


@dataclass
class SymbolTable:
    """Symbols of one file plus node/scope lookup structures."""

    module_symbol: SymbolDraft
    drafts: list[SymbolDraft] = field(default_factory=list)
    # id(ast node) -> qualified name of the symbol the node defines
    node_to_qualified: dict[int, str] = field(default_factory=dict)
    by_qualified: dict[tuple[str, SymbolKind], SymbolDraft] = field(default_factory=dict)

    def get(self, qualified_name: str, kind: SymbolKind | None = None) -> SymbolDraft | None:
        if kind is not None:
            return self.by_qualified.get((qualified_name, kind))
        for (name, _kind), draft in self.by_qualified.items():
            if name == qualified_name:
                return draft
        return None

    def methods_of(self, qualified_name: str) -> dict[str, SymbolDraft]:
        """Direct method symbols of a class, keyed by simple name."""
        prefix = qualified_name + "."
        out: dict[str, SymbolDraft] = {}
        for draft in self.drafts:
            if draft.kind is SymbolKind.METHOD and draft.qualified_name.startswith(prefix):
                remainder = draft.qualified_name[len(prefix) :]
                if "." not in remainder:
                    out.setdefault(draft.name, draft)
        return out


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # unparse is total for stdlib nodes; guard anyway
        return None


def _extract_parameters(args: ast.arguments) -> list[ParameterInfo]:
    params: list[ParameterInfo] = []

    def add(arg: ast.arg, kind: str, default: ast.AST | None) -> None:
        params.append(
            ParameterInfo(
                name=arg.arg,
                kind=kind,
                annotation=_unparse(arg.annotation),
                default=_unparse(default),
            )
        )

    positional = [*args.posonlyargs, *args.args]
    defaults = list(args.defaults)
    # Defaults apply to the last N positional parameters.
    offset = len(positional) - len(defaults)
    for index, arg in enumerate(positional):
        kind = "posonly" if index < len(args.posonlyargs) else "arg"
        default = defaults[index - offset] if index >= offset else None
        add(arg, kind, default)

    if args.vararg is not None:
        add(args.vararg, "vararg", None)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        add(arg, "kwonly", default)
    if args.kwarg is not None:
        add(args.kwarg, "kwarg", None)
    return params


def _signature_text(args: ast.arguments, returns: ast.AST | None) -> str:
    args_text = _unparse(args) or ""
    text = f"({args_text})"
    if returns is not None:
        text += f" -> {_unparse(returns) or '?'}"
    return text


def _start_line(node: ast.AST, decorators: Sequence[ast.AST]) -> int:
    base = getattr(node, "lineno", 1)
    if decorators:
        first_decorator = min(getattr(d, "lineno", base) for d in decorators)
        return min(base, first_decorator)
    return base


class _SymbolExtractor:
    """Walks a parsed module and produces its symbol table."""

    def __init__(self, tree: ast.Module, module_name: str, source_line_count: int) -> None:
        self._tree = tree
        self._module_name = module_name
        self._line_count = source_line_count
        self._ordinals: Counter[tuple[str, SymbolKind]] = Counter()
        self.drafts: list[SymbolDraft] = []
        self.node_to_qualified: dict[int, str] = {}
        self.by_qualified: dict[tuple[str, SymbolKind], SymbolDraft] = {}

    def run(self) -> SymbolTable:
        module_name = self._module_name
        simple = module_name.rsplit(".", 1)[-1]
        module_draft = self._make(
            name=simple,
            qualified=module_name,
            kind=SymbolKind.MODULE,
            parent_qualified=None,
            start_line=1,
            end_line=self._line_count,
            docstring=ast.get_docstring(self._tree, clean=True),
        )
        self.node_to_qualified[id(self._tree)] = module_name
        self._walk_body(self._tree.body, parent_qualified=module_name, parent_is_class=False)
        return SymbolTable(
            module_symbol=module_draft,
            drafts=list(self.drafts),
            node_to_qualified=dict(self.node_to_qualified),
            by_qualified=dict(self.by_qualified),
        )

    def _make(
        self,
        *,
        name: str,
        qualified: str,
        kind: SymbolKind,
        parent_qualified: str | None,
        start_line: int,
        end_line: int,
        docstring: str | None = None,
        signature: str | None = None,
        parameters: list[ParameterInfo] | None = None,
        return_annotation: str | None = None,
        bases: list[str] | None = None,
        decorators: list[str] | None = None,
    ) -> SymbolDraft:
        ordinal = self._ordinals[(qualified, kind)]
        self._ordinals[(qualified, kind)] += 1
        draft = SymbolDraft(
            name=name,
            qualified_name=qualified,
            kind=kind,
            ordinal=ordinal,
            start_line=start_line,
            end_line=end_line,
            parent_qualified=parent_qualified,
            docstring=docstring,
            signature=signature,
            parameters=parameters or [],
            return_annotation=return_annotation,
            bases=bases or [],
            decorators=decorators or [],
        )
        self.drafts.append(draft)
        self.by_qualified.setdefault((qualified, kind), draft)
        return draft

    def _walk_body(
        self, body: list[ast.stmt], *, parent_qualified: str, parent_is_class: bool
    ) -> None:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                self._class(stmt, parent_qualified, parent_is_class)
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                self._function(stmt, parent_qualified, parent_is_class)
            elif isinstance(stmt, ast.If):
                # Conditional top-level definitions (e.g. version guards).
                self._walk_body(
                    stmt.body, parent_qualified=parent_qualified, parent_is_class=parent_is_class
                )
                self._walk_body(
                    stmt.orelse, parent_qualified=parent_qualified, parent_is_class=parent_is_class
                )
            elif isinstance(stmt, ast.Try):
                self._walk_body(
                    stmt.body, parent_qualified=parent_qualified, parent_is_class=parent_is_class
                )
                self._walk_body(
                    stmt.orelse, parent_qualified=parent_qualified, parent_is_class=parent_is_class
                )
                self._walk_body(
                    stmt.finalbody,
                    parent_qualified=parent_qualified,
                    parent_is_class=parent_is_class,
                )
                for handler in stmt.handlers:
                    self._walk_body(
                        handler.body,
                        parent_qualified=parent_qualified,
                        parent_is_class=parent_is_class,
                    )
            elif isinstance(stmt, ast.With | ast.AsyncWith):
                self._walk_body(
                    stmt.body, parent_qualified=parent_qualified, parent_is_class=parent_is_class
                )
            elif isinstance(stmt, ast.For | ast.AsyncFor):
                self._walk_body(
                    stmt.body, parent_qualified=parent_qualified, parent_is_class=parent_is_class
                )
                self._walk_body(
                    stmt.orelse, parent_qualified=parent_qualified, parent_is_class=parent_is_class
                )
            elif isinstance(stmt, ast.While):
                self._walk_body(
                    stmt.body, parent_qualified=parent_qualified, parent_is_class=parent_is_class
                )
                self._walk_body(
                    stmt.orelse, parent_qualified=parent_qualified, parent_is_class=parent_is_class
                )

    def _class(self, node: ast.ClassDef, parent_qualified: str, parent_is_class: bool) -> None:
        del parent_is_class  # class nodes are always SymbolKind.CLASS
        qualified = f"{parent_qualified}.{node.name}"
        draft = self._make(
            name=node.name,
            qualified=qualified,
            kind=SymbolKind.CLASS,
            parent_qualified=parent_qualified,
            start_line=_start_line(node, node.decorator_list),
            end_line=getattr(node, "end_lineno", node.lineno),
            docstring=ast.get_docstring(node, clean=True),
            bases=[text for text in (_unparse(b) for b in node.bases) if text is not None],
            decorators=[
                text for text in (_unparse(d) for d in node.decorator_list) if text is not None
            ],
        )
        self.node_to_qualified[id(node)] = qualified
        # Nested classes inside functions use the function's qualified name as parent.
        self._walk_body(node.body, parent_qualified=qualified, parent_is_class=True)
        del draft

    def _function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_qualified: str,
        parent_is_class: bool,
    ) -> None:
        qualified = f"{parent_qualified}.{node.name}"
        kind = SymbolKind.METHOD if parent_is_class else SymbolKind.FUNCTION
        parameters = _extract_parameters(node.args)
        signature = _signature_text(node.args, node.returns)
        self._make(
            name=node.name,
            qualified=qualified,
            kind=kind,
            parent_qualified=parent_qualified,
            start_line=_start_line(node, node.decorator_list),
            end_line=getattr(node, "end_lineno", node.lineno),
            docstring=ast.get_docstring(node, clean=True),
            signature=signature,
            parameters=parameters,
            return_annotation=_unparse(node.returns),
            decorators=[
                text for text in (_unparse(d) for d in node.decorator_list) if text is not None
            ],
        )
        self.node_to_qualified[id(node)] = qualified
        self._walk_body(node.body, parent_qualified=qualified, parent_is_class=False)


def extract_symbols(tree: ast.Module, module_name: str, source_line_count: int) -> SymbolTable:
    """Extract all symbols from a parsed module."""
    return _SymbolExtractor(tree, module_name, source_line_count).run()
