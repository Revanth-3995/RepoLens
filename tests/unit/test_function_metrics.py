"""Phase 2 slice 1: function-level structural metrics."""

from __future__ import annotations

import ast

from repolens.analysis.function_metrics import extract_function_metrics
from repolens.analysis.symbols import extract_symbols


def _extract(source: str):
    tree = ast.parse(source)
    table = extract_symbols(tree, "m", len(source.splitlines()))
    return extract_function_metrics(tree, table, "m")


def test_simple_function_metrics():
    metrics = _extract("def f(x, y):\n    return x + y\n")
    assert len(metrics) == 1
    m = metrics[0]
    assert m.qualified_name == "m.f"
    assert m.parameter_count == 2
    assert m.statement_count == 1
    assert m.return_count == 1
    assert m.cyclomatic_complexity == 1
    assert m.max_nesting_depth == 0


def test_branches_increase_complexity():
    source = (
        "def g(a, b):\n"
        "    if a > 0 and b > 0:\n"
        "        return 1\n"
        "    for i in range(a):\n"
        "        if i == b:\n"
        "            return 2\n"
        "    return 3\n"
    )
    metrics = _extract(source)
    m = metrics[0]
    # 1 (base) + 1 (if) + 1 (and) + 1 (for) + 1 (if) = 5
    assert m.cyclomatic_complexity == 5
    assert m.statement_count == 6
    assert m.if_count == 2
    assert m.for_count == 1
    assert m.return_count == 3
    assert m.max_nesting_depth >= 2


def test_nested_function_is_separate_metric():
    source = "def outer():\n    def inner():\n        return 1\n    return inner()\n"
    metrics = _extract(source)
    names = {m.qualified_name for m in metrics}
    assert names == {"m.outer", "m.outer.inner"}
    by_name = {m.qualified_name: m for m in metrics}
    assert by_name["m.outer"].statement_count == 2
    assert by_name["m.outer.inner"].statement_count == 1


def test_nested_definition_expressions_count_in_enclosing_scope():
    source = (
        "def outer():\n"
        "    @decorate()\n"
        "    def inner(x=factory()):\n"
        "        return hidden()\n"
        "    class Nested(make_base()):\n"
        "        def method(self):\n"
        "            return also_hidden()\n"
        "    return inner\n"
    )
    by_name = {m.qualified_name: m for m in _extract(source)}

    # Decorators, defaults, and class bases execute in outer's scope. Nested
    # bodies remain separate functions/methods for metrics.
    assert by_name["m.outer"].statement_count == 3
    assert by_name["m.outer"].call_count == 3
    assert by_name["m.outer.inner"].call_count == 1
    assert by_name["m.outer.Nested.method"].call_count == 1


def test_empty_module_yields_no_metrics():
    assert _extract("x = 1\n") == []
