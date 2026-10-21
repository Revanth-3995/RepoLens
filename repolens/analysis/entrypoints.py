"""Deterministic entry-point detection from Python source (Phase 2).

An entry point is only reported when the source contains an explicit
``if __name__ == "__main__":`` guard, or when the file is named
``__main__.py``. No naming heuristics such as ``main.py`` are claimed as a
runtime entry point; those are left to the caller's judgement.
"""

from __future__ import annotations

import ast

from repolens.core.models import EntryPoint


def _is_main_guard(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    for name_expr, value_expr in ((left, right), (right, left)):
        if (
            isinstance(name_expr, ast.Name)
            and name_expr.id == "__name__"
            and isinstance(value_expr, ast.Constant)
            and value_expr.value == "__main__"
        ):
            return True
    return False


def detect_entry_points(tree: ast.Module, rel_path: str) -> list[EntryPoint]:
    """Return explicit ``__main__`` guards and ``__main__.py`` facts."""
    out: list[EntryPoint] = []
    if rel_path.replace("\\", "/").rsplit("/", 1)[-1] == "__main__.py":
        out.append(
            EntryPoint(
                rel_path=rel_path,
                line=1,
                evidence="file is named __main__.py",
            )
        )
    for stmt in tree.body:
        if isinstance(stmt, ast.If) and _is_main_guard(stmt.test):
            out.append(
                EntryPoint(
                    rel_path=rel_path,
                    line=stmt.lineno,
                    evidence="explicit if __name__ == '__main__' guard",
                )
            )
    return out
