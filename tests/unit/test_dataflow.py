"""P2-03: exact tests for bounded local reaching definitions and def-use."""

from __future__ import annotations

import ast

from repolens.analysis.cfg import build_function_cfg_with_facts
from repolens.analysis.dataflow import analyze_dataflow
from repolens.analysis.symbols import extract_symbols
from repolens.core.models import DataFlowResult, DefinitionKind


def _df(source: str, name: str = "f", **kwargs) -> DataFlowResult:
    tree = ast.parse(source)
    table = extract_symbols(tree, "m", len(source.splitlines()))
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == name
        and id(n) in table.node_to_symbol
    )
    built = build_function_cfg_with_facts(
        node,
        source.splitlines(),
        symbol_id="sym_test",
        qualified_name=f"m.{name}",
        file_id="file_test",
        rel_path="m.py",
    )
    return analyze_dataflow(built, **kwargs)


def _reaching(df: DataFlowResult, use_name: str, line: int) -> list[str]:
    use = next(use for use in df.uses if use.name == use_name and use.line == line)
    return next(item.definitions for item in df.reaching if item.use_id == use.id)


def test_parameters_are_definitions_and_reach_uses():
    df = _df("def f(a, b):\n    return a + b\n")
    assert [(d.name, d.kind) for d in df.definitions] == [
        ("a", DefinitionKind.PARAMETER),
        ("b", DefinitionKind.PARAMETER),
    ]
    assert [(u.name, u.line) for u in df.uses] == [("a", 2), ("b", 2)]
    assert _reaching(df, "a", 2) == ["d0"]
    assert _reaching(df, "b", 2) == ["d1"]
    assert df.undefined_uses == []
    assert df.def_use[0].uses == ["u0"]
    assert df.def_use[1].uses == ["u1"]


def test_reassignment_kills_the_previous_definition():
    df = _df("def f():\n    x = 1\n    x = 2\n    return x\n")
    assert [(d.id, d.name, d.line) for d in df.definitions] == [
        ("d0", "x", 2),
        ("d1", "x", 3),
    ]
    assert _reaching(df, "x", 4) == ["d1"]
    assert df.def_use[0].uses == []
    assert df.def_use[1].uses == ["u0"]


def test_branch_join_unions_reaching_definitions():
    df = _df("def f(a):\n    if a:\n        x = 1\n    else:\n        x = 2\n    return x\n")
    assert _reaching(df, "x", 6) == ["d1", "d2"]


def test_loop_fixpoint_keeps_entry_and_back_edge_definitions():
    df = _df("def f():\n    x = 0\n    for i in range(3):\n        x = x + 1\n    return x\n")
    assert [(d.name, d.kind) for d in df.definitions] == [
        ("x", DefinitionKind.ASSIGNMENT),
        ("i", DefinitionKind.LOOP_TARGET),
        ("x", DefinitionKind.ASSIGNMENT),
    ]
    # Inside the loop body, x may come from before the loop or the prior iteration.
    assert _reaching(df, "x", 4) == ["d0", "d2"]
    # After the loop, the zero-iteration and executed paths are both possible.
    assert _reaching(df, "x", 5) == ["d0", "d2"]
    assert df.def_use[0].uses == ["u1", "u2"]
    assert df.def_use[2].uses == ["u1", "u2"]


def test_read_before_local_definition_is_undefined_locally():
    df = _df("def f():\n    print(x)\n    x = 1\n")
    assert [(u.name, u.line) for u in df.uses] == [("print", 2), ("x", 2)]
    assert _reaching(df, "x", 2) == []
    assert len(df.undefined_uses) == 2
    assert any("globals, builtins" in note for note in df.notes)


def test_augmented_assignment_reads_before_its_own_definition():
    df = _df("def f():\n    x = 1\n    x += 1\n    return x\n")
    assert [(d.name, d.kind, d.line) for d in df.definitions] == [
        ("x", DefinitionKind.ASSIGNMENT, 2),
        ("x", DefinitionKind.AUGMENTED_ASSIGNMENT, 3),
    ]
    assert _reaching(df, "x", 3) == ["d0"]
    assert _reaching(df, "x", 4) == ["d1"]
    assert any("augmented-assignment" in note for note in df.notes)


def test_with_target_is_a_definition_and_reaches_body():
    df = _df("def f(p):\n    with open(p) as fh:\n        return fh.read()\n")
    assert [(d.name, d.kind) for d in df.definitions] == [
        ("p", DefinitionKind.PARAMETER),
        ("fh", DefinitionKind.WITH_TARGET),
    ]
    assert _reaching(df, "fh", 3) == ["d1"]
    assert "open" in {u.name for u in df.uses}


def test_except_name_is_a_definition_for_the_handler_body():
    df = _df("def f():\n    try:\n        x = 1\n    except ValueError as e:\n        return e\n")
    assert ("e", DefinitionKind.EXCEPT_NAME) in [(d.name, d.kind) for d in df.definitions]
    assert _reaching(df, "e", 5) == ["d1"]
    assert "ValueError" in {u.name for u in df.uses}


def test_global_name_is_declared_non_local_and_not_undefined():
    df = _df("def f():\n    global g\n    g = 1\n    return g\n")
    assert df.non_local_names == ["g"]
    assert _reaching(df, "g", 4) == ["d0"]
    assert df.undefined_uses == []


def test_nonlocal_name_is_declared_non_local_and_not_undefined():
    df = _df(
        "def outer():\n    x = 1\n    def inner():\n        nonlocal x\n        return x\n",
        name="inner",
    )
    assert df.non_local_names == ["x"]
    assert df.definitions == []
    assert [(u.name, u.line) for u in df.uses] == [("x", 5)]
    assert df.undefined_uses == []


def test_match_pattern_binding_reaches_guard_and_body():
    df = _df("def f(x):\n    match x:\n        case n if n > 0:\n            return n\n")
    assert ("n", DefinitionKind.PATTERN) in [(d.name, d.kind) for d in df.definitions]
    assert _reaching(df, "n", 3) == ["d1"]
    assert _reaching(df, "n", 4) == ["d1"]


def test_comprehension_and_lambda_interiors_are_not_analysed():
    df = _df("def f(xs):\n    ys = [x for x in xs]\n    z = lambda a: a\n    return ys, z\n")
    assert "x" not in {u.name for u in df.uses}
    assert "a" not in {u.name for u in df.uses}
    assert "xs" in {u.name for u in df.uses}
    assert any("comprehension interiors" in note for note in df.notes)


def test_tuple_targets_are_individual_definitions():
    df = _df("def f(pair):\n    a, b = pair\n    return a + b\n")
    assert [d.name for d in df.definitions] == ["pair", "a", "b"]
    assert _reaching(df, "a", 3) == ["d1"]
    assert _reaching(df, "b", 3) == ["d2"]


def test_walrus_target_is_a_definition_for_later_uses():
    df = _df("def f(v):\n    if (n := v):\n        return n\n")
    assert ("n", DefinitionKind.WALRUS) in [(d.name, d.kind) for d in df.definitions]
    assert _reaching(df, "n", 3) == ["d1"]


def test_use_after_walrus_target_in_same_expression_sees_definition():
    df = _df("def f():\n    a = (n := 1) + n\n    return a\n")
    assert _reaching(df, "n", 2) == ["d1"]
    assert df.undefined_uses == []


def test_use_before_a_later_walrus_in_the_same_expression_does_not_see_it():
    df = _df("def f():\n    a = (n := 1) + n + (n := 2)\n    return a\n")
    use = next(use for use in df.uses if use.name == "n")
    reaching = next(item.definitions for item in df.reaching if item.use_id == use.id)
    assert reaching == ["d1"]
    assert df.def_use[1].uses == ["u0"]


def test_definition_cap_reports_truncation():
    df = _df("def f():\n    a = 1\n    b = 2\n    return a + b\n", max_definitions=1)
    assert df.truncated is True
    assert len(df.definitions) == 1
    assert any("cap reached" in note for note in df.notes)


def test_cfg_truncation_propagates_into_dataflow():
    source = "def f(x):\n    if x:\n        return 1\n    return 0\n"
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    built = build_function_cfg_with_facts(
        node,
        source.splitlines(),
        symbol_id="sym_test",
        qualified_name="m.f",
        file_id="file_test",
        rel_path="m.py",
        max_nodes=3,
    )
    df = analyze_dataflow(built)
    assert built.cfg.truncated is True
    assert df.truncated is True
    assert any("node cap" in note for note in df.notes)
    assert not any("definition/use collection cap" in note for note in df.notes)
