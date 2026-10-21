"""Function-level structural metrics (Phase 2, slice 1).

Deterministic, evidence-backed metrics derived from a function's AST:
statement counts, nesting depth, and a cyclomatic-complexity estimate.
No inference, no ML, no execution of analyzed code.
"""

from __future__ import annotations

import ast
from typing import Any, cast

from repolens.analysis.symbols import SymbolTable
from repolens.core.models import FunctionMetricsDraft


class _Walker:
    """Counts statements/control-flow for a single function scope.

    ``statement_count`` counts every ``ast.stmt`` in the function's own scope,
    including compound-statement headers and a nested ``def``/``class``
    statement itself. Nested function/class/lambda bodies are deliberately not
    descended into, so their statements do not inflate the enclosing
    function's metrics.
    """

    def __init__(self) -> None:
        self.statement_count = 0
        self.if_count = 0
        self.for_count = 0
        self.while_count = 0
        self.try_count = 0
        self.with_count = 0
        self.match_count = 0
        self.return_count = 0
        self.call_count = 0
        self.bool_ops = 0
        self.except_count = 0
        self.max_depth = 0

    def _walk_definition_expressions(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, depth: int
    ) -> None:
        """Count calls in nested-definition expressions evaluated in this scope.

        Decorators, defaults, class bases, and class keywords are evaluated in
        the enclosing scope when the nested definition executes. Their bodies
        remain separate scopes and are not descended into.
        """
        for decorator in node.decorator_list:
            self.walk(decorator, depth)
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                self.walk(base, depth)
            for keyword in node.keywords:
                self.walk(keyword.value, depth)
            return
        for default in node.args.defaults:
            self.walk(default, depth)
        for kw_default in node.args.kw_defaults:
            if kw_default is not None:
                self.walk(kw_default, depth)

    def walk(self, node: Any, depth: int = 0) -> None:
        if isinstance(node, ast.stmt):
            self.statement_count += 1
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self._walk_definition_expressions(node, depth)
            return
        if isinstance(node, ast.Lambda):
            return

        if isinstance(node, ast.If):
            self.if_count += 1
            self.max_depth = max(self.max_depth, depth + 1)
            self.walk(node.test, depth)
            for stmt in node.body:
                self.walk(stmt, depth + 1)
            # elif is an If nested in orelse -> treat as a sibling decision.
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                self.walk(node.orelse[0], depth)
            else:
                for stmt in node.orelse:
                    self.walk(stmt, depth + 1)
            return

        if isinstance(node, (ast.For, ast.AsyncFor)):
            self.for_count += 1
            self.max_depth = max(self.max_depth, depth + 1)
            self.walk(node.iter, depth)
            for stmt in node.body:
                self.walk(stmt, depth + 1)
            for stmt in node.orelse:
                self.walk(stmt, depth + 1)
            return

        if isinstance(node, ast.While):
            self.while_count += 1
            self.max_depth = max(self.max_depth, depth + 1)
            self.walk(node.test, depth)
            for stmt in node.body:
                self.walk(stmt, depth + 1)
            for stmt in node.orelse:
                self.walk(stmt, depth + 1)
            return

        _ast = cast(Any, ast)
        if isinstance(node, ast.Try) or (
            hasattr(ast, "TryStar") and isinstance(node, _ast.TryStar)
        ):
            self.try_count += 1
            self.max_depth = max(self.max_depth, depth + 1)
            for stmt in node.body:
                self.walk(stmt, depth + 1)
            for handler in node.handlers:
                self.except_count += 1
                if handler.type is not None:
                    self.walk(handler.type, depth)
                for stmt in handler.body:
                    self.walk(stmt, depth + 1)
            for stmt in node.orelse:
                self.walk(stmt, depth + 1)
            for stmt in node.finalbody:
                self.walk(stmt, depth + 1)
            return

        if isinstance(node, (ast.With, ast.AsyncWith)):
            self.with_count += 1
            self.max_depth = max(self.max_depth, depth + 1)
            for item in node.items:
                self.walk(item.context_expr, depth)
            for stmt in node.body:
                self.walk(stmt, depth + 1)
            return

        if getattr(ast, "Match", None) is not None and isinstance(node, ast.Match):
            self.match_count += len(node.cases)
            self.max_depth = max(self.max_depth, depth + 1)
            self.walk(node.subject, depth)
            for case in node.cases:
                if case.guard is not None:
                    self.walk(case.guard, depth + 1)
                for stmt in case.body:
                    self.walk(stmt, depth + 1)
            return

        if isinstance(node, ast.Return):
            self.return_count += 1
        if isinstance(node, ast.Call):
            self.call_count += 1
        if isinstance(node, ast.BoolOp):
            self.bool_ops += len(node.values) - 1

        for child in ast.iter_child_nodes(node):
            self.walk(child, depth)


def _walk_function(node: Any) -> FunctionMetricsDraft:
    w = _Walker()
    for stmt in node.body:
        w.walk(stmt, 0)
    param_count = (
        len(node.args.posonlyargs)
        + len(node.args.args)
        + len(node.args.kwonlyargs)
        + (1 if node.args.vararg else 0)
        + (1 if node.args.kwarg else 0)
    )
    end_line = getattr(node, "end_lineno", None) or node.lineno
    line_count = end_line - node.lineno + 1
    cyclomatic = (
        1 + w.if_count + w.for_count + w.while_count + w.except_count + w.match_count + w.bool_ops
    )
    return FunctionMetricsDraft(
        qualified_name="",
        start_line=node.lineno,
        end_line=end_line,
        parameter_count=param_count,
        line_count=line_count,
        statement_count=w.statement_count,
        if_count=w.if_count,
        for_count=w.for_count,
        while_count=w.while_count,
        try_count=w.try_count,
        with_count=w.with_count,
        match_count=w.match_count,
        return_count=w.return_count,
        call_count=w.call_count,
        max_nesting_depth=w.max_depth,
        cyclomatic_complexity=cyclomatic,
    )


def extract_function_metrics(
    tree: ast.Module, table: SymbolTable, module_name: str
) -> list[FunctionMetricsDraft]:
    """Extract structural metrics for every function/method in ``tree``."""
    node_to_q = table.node_to_qualified
    out: list[FunctionMetricsDraft] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        q = node_to_q.get(id(node))
        if q is None:
            continue
        m = _walk_function(node)
        m.qualified_name = q
        symbol = table.node_to_symbol.get(id(node))
        if symbol is not None:
            m.symbol_id = symbol.symbol_id
        out.append(m)
    return out
