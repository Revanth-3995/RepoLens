"""Python AST parsing with per-file error isolation."""

from __future__ import annotations

import ast

from repolens.core.errors import ParseError


def parse_source(source: str, rel_path: str) -> ast.Module:
    """Parse Python source into an AST.

    Raises :class:`ParseError` with the one-based line and offset reported by
    ``ast`` so a malformed file can be recorded without aborting the run.
    """
    try:
        return ast.parse(source, filename=rel_path, mode="exec")
    except SyntaxError as exc:
        raise ParseError(
            message=exc.msg or "syntax error",
            line=exc.lineno,
            offset=exc.offset,
        ) from None
    except (ValueError, RecursionError) as exc:
        # e.g. source containing null bytes or pathological nesting depth
        raise ParseError(message=str(exc), line=None, offset=None) from None
