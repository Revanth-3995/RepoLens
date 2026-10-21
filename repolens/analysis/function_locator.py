"""Locate the AST node for a persisted function/method symbol.

Symbol IDs are assigned after extraction, so a re-parse cannot reproduce them
directly. The stable, persisted identity tuple is
``(qualified_name, kind, ordinal)`` plus the line range. This module matches on
that tuple and uses the line range as a tie-breaker for duplicate definitions.
"""

from __future__ import annotations

import ast

from repolens.analysis.symbols import SymbolTable
from repolens.core.models import SymbolDraft, SymbolKind, SymbolRecord


def find_function_node(
    tree: ast.Module, table: SymbolTable, symbol: SymbolRecord
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the AST function node the persisted symbol describes, if present."""
    if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
        return None
    candidates: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, SymbolDraft]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        draft = table.node_to_symbol.get(id(node))
        if draft is None:
            continue
        if (
            draft.qualified_name == symbol.qualified_name
            and draft.kind is symbol.kind
            and draft.ordinal == symbol.ordinal
        ):
            candidates.append((node, draft))
    if not candidates:
        return None
    for node, draft in candidates:
        if draft.start_line == symbol.start_line and draft.end_line == symbol.end_line:
            return node
    for node, _draft in candidates:
        if getattr(node, "end_lineno", None) == symbol.end_line:
            return node
    return candidates[0][0]
