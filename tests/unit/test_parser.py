"""T7: AST parsing and per-file failure isolation."""

from __future__ import annotations

import ast

import pytest

from repolens.analysis.python_parser import parse_source
from repolens.core.errors import ParseError


def test_parses_valid_source():
    tree = parse_source("x = 1\n", "a.py")
    assert isinstance(tree, ast.Module)
    assert len(tree.body) == 1


def test_syntax_error_reports_line_and_offset():
    with pytest.raises(ParseError) as excinfo:
        parse_source("def f(:\n    pass\n", "broken.py")
    err = excinfo.value
    assert err.line == 1
    assert err.offset is not None
    assert err.message


def test_null_bytes_are_isolated():
    with pytest.raises(ParseError):
        parse_source("x = 1\x00\n", "null.py")
