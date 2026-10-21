"""P2-02: exact structural tests for the per-function CFG."""

from __future__ import annotations

import ast

from repolens.analysis.cfg import CFGWithFacts, build_function_cfg_with_facts
from repolens.analysis.symbols import extract_symbols
from repolens.core.models import CFGEdgeType, CFGNodeKind


def _build(source: str, name: str = "f", **kwargs) -> CFGWithFacts:
    tree = ast.parse(source)
    table = extract_symbols(tree, "m", len(source.splitlines()))
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == name
        and id(n) in table.node_to_symbol
    )
    return build_function_cfg_with_facts(
        node,
        source.splitlines(),
        symbol_id="sym_test",
        qualified_name=f"m.{name}",
        file_id="file_test",
        rel_path="m.py",
        **kwargs,
    )


def _triples(built: CFGWithFacts) -> list[tuple[str, str, CFGEdgeType]]:
    return [(edge.source, edge.target, edge.type) for edge in built.cfg.edges]


def test_straight_line_exact_structure():
    built = _build("def f(a):\n    x = 1\n    return x\n")
    cfg = built.cfg
    assert cfg.entry == "n0"
    assert cfg.exit == "n1"
    assert [node.kind for node in cfg.nodes] == [
        CFGNodeKind.ENTRY,
        CFGNodeKind.EXIT,
        CFGNodeKind.STATEMENT,
        CFGNodeKind.RETURN,
    ]
    assert _triples(built) == [
        ("n0", "n2", CFGEdgeType.NORMAL),
        ("n2", "n3", CFGEdgeType.NORMAL),
        ("n3", "n1", CFGEdgeType.RETURN),
    ]
    assert cfg.nodes[2].line == 2
    assert cfg.nodes[2].source == "x = 1"
    assert cfg.nodes[3].line == 3
    assert cfg.nodes[3].source == "return x"
    assert cfg.start_line == 1
    assert cfg.end_line == 3


def test_if_else_exact_merge_and_edges():
    built = _build("def f(a):\n    if a:\n        x = 1\n    else:\n        x = 2\n    return x\n")
    assert [node.kind for node in built.cfg.nodes] == [
        CFGNodeKind.ENTRY,
        CFGNodeKind.EXIT,
        CFGNodeKind.CONDITION,
        CFGNodeKind.STATEMENT,
        CFGNodeKind.STATEMENT,
        CFGNodeKind.MERGE,
        CFGNodeKind.RETURN,
    ]
    assert _triples(built) == [
        ("n0", "n2", CFGEdgeType.NORMAL),
        ("n2", "n3", CFGEdgeType.TRUE),
        ("n2", "n4", CFGEdgeType.FALSE),
        ("n3", "n5", CFGEdgeType.NORMAL),
        ("n4", "n5", CFGEdgeType.NORMAL),
        ("n5", "n6", CFGEdgeType.NORMAL),
        ("n6", "n1", CFGEdgeType.RETURN),
    ]


def test_if_without_else_has_false_edge_to_merge():
    built = _build("def f(a):\n    if a:\n        x = 1\n    return x\n")
    triples = _triples(built)
    assert ("n2", "n3", CFGEdgeType.TRUE) in triples
    assert ("n2", "n4", CFGEdgeType.FALSE) in triples
    assert ("n2", "n4", CFGEdgeType.TRUE) not in triples
    assert ("n3", "n4", CFGEdgeType.NORMAL) in triples


def test_elif_is_a_sibling_condition_with_own_merge():
    built = _build(
        "def f(a, b):\n"
        "    if a:\n"
        "        x = 1\n"
        "    elif b:\n"
        "        x = 2\n"
        "    else:\n"
        "        x = 3\n"
        "    return x\n"
    )
    triples = _triples(built)
    assert ("n2", "n4", CFGEdgeType.FALSE) in triples
    assert ("n4", "n5", CFGEdgeType.TRUE) in triples
    assert ("n4", "n6", CFGEdgeType.FALSE) in triples
    assert ("n5", "n7", CFGEdgeType.NORMAL) in triples
    assert ("n6", "n7", CFGEdgeType.NORMAL) in triples
    assert ("n3", "n8", CFGEdgeType.NORMAL) in triples
    assert ("n7", "n8", CFGEdgeType.NORMAL) in triples


def test_loop_break_continue_and_back_edge_exact():
    built = _build(
        "def f(xs):\n"
        "    for x in xs:\n"
        "        if x:\n"
        "            continue\n"
        "        break\n"
        "    return x\n"
    )
    assert _triples(built) == [
        ("n0", "n2", CFGEdgeType.NORMAL),
        ("n2", "n4", CFGEdgeType.LOOP_BODY),
        ("n4", "n5", CFGEdgeType.TRUE),
        ("n5", "n2", CFGEdgeType.CONTINUE),
        ("n4", "n6", CFGEdgeType.FALSE),
        ("n6", "n7", CFGEdgeType.NORMAL),
        ("n7", "n3", CFGEdgeType.BREAK),
        ("n2", "n3", CFGEdgeType.LOOP_EXIT),
        ("n3", "n8", CFGEdgeType.NORMAL),
        ("n8", "n1", CFGEdgeType.RETURN),
    ]


def test_while_loop_back_edge():
    built = _build("def f(a):\n    while a:\n        a = a - 1\n    return a\n")
    triples = _triples(built)
    assert ("n2", "n4", CFGEdgeType.LOOP_BODY) in triples
    assert ("n4", "n2", CFGEdgeType.LOOP_BACK) in triples
    assert ("n2", "n3", CFGEdgeType.LOOP_EXIT) in triples
    assert ("n3", "n5", CFGEdgeType.NORMAL) in triples


def test_try_except_else_finally_exact():
    built = _build(
        "def f():\n"
        "    try:\n"
        "        x = 1\n"
        "    except ValueError:\n"
        "        x = 2\n"
        "    else:\n"
        "        x = 3\n"
        "    finally:\n"
        "        y = 4\n"
        "    return x\n"
    )
    assert _triples(built) == [
        ("n0", "n2", CFGEdgeType.NORMAL),
        ("n2", "n3", CFGEdgeType.NORMAL),
        ("n2", "n4", CFGEdgeType.EXCEPTION),
        ("n3", "n4", CFGEdgeType.EXCEPTION),
        ("n4", "n5", CFGEdgeType.NORMAL),
        ("n3", "n6", CFGEdgeType.NORMAL),
        ("n5", "n7", CFGEdgeType.FINALLY),
        ("n6", "n7", CFGEdgeType.FINALLY),
        ("n7", "n8", CFGEdgeType.NORMAL),
        ("n8", "n9", CFGEdgeType.NORMAL),
        ("n9", "n10", CFGEdgeType.NORMAL),
        ("n10", "n1", CFGEdgeType.RETURN),
    ]
    exception_edges = [edge for edge in built.cfg.edges if edge.type is CFGEdgeType.EXCEPTION]
    assert {edge.source for edge in exception_edges} == {"n2", "n3"}
    assert all("conservative" in edge.evidence for edge in exception_edges)
    assert any("conservative" in note for note in built.cfg.notes)


def test_try_without_finally_joins_handler_and_normal_paths():
    built = _build(
        "def f():\n    try:\n        x = 1\n    except ValueError:\n        x = 2\n    return x\n"
    )
    triples = _triples(built)
    assert ("n2", "n3", CFGEdgeType.NORMAL) in triples
    assert ("n3", "n2", CFGEdgeType.EXCEPTION) in triples or (
        "n3",
        "n4",
        CFGEdgeType.EXCEPTION,
    ) in triples
    # normal and handler paths both join the try merge before the return
    assert any(edge.type is CFGEdgeType.NORMAL and edge.source == "n3" for edge in built.cfg.edges)
    assert any(edge.type is CFGEdgeType.NORMAL and edge.source == "n5" for edge in built.cfg.edges)


def test_with_and_join():
    built = _build(
        "def f(p):\n    with open(p) as fh:\n        data = fh.read()\n    return data\n"
    )
    assert [node.kind for node in built.cfg.nodes] == [
        CFGNodeKind.ENTRY,
        CFGNodeKind.EXIT,
        CFGNodeKind.WITH,
        CFGNodeKind.STATEMENT,
        CFGNodeKind.MERGE,
        CFGNodeKind.RETURN,
    ]
    assert "with open(p)" in built.cfg.nodes[2].label
    assert _triples(built) == [
        ("n0", "n2", CFGEdgeType.NORMAL),
        ("n2", "n3", CFGEdgeType.NORMAL),
        ("n3", "n4", CFGEdgeType.NORMAL),
        ("n4", "n5", CFGEdgeType.NORMAL),
        ("n5", "n1", CFGEdgeType.RETURN),
    ]


def test_match_exhaustive_has_no_no_match_edge():
    built = _build(
        "def f(x):\n"
        "    match x:\n"
        "        case 1:\n"
        "            y = 1\n"
        "        case _:\n"
        "            y = 2\n"
        "    return y\n"
    )
    triples = _triples(built)
    assert ("n2", "n4", CFGEdgeType.CASE) in triples
    assert ("n2", "n6", CFGEdgeType.CASE) in triples
    assert all(edge.type is not CFGEdgeType.NO_MATCH for edge in built.cfg.edges)
    assert ("n5", "n3", CFGEdgeType.NORMAL) in triples
    assert ("n7", "n3", CFGEdgeType.NORMAL) in triples


def test_match_non_exhaustive_has_no_match_edge():
    built = _build("def f(x):\n    match x:\n        case 1:\n            y = 1\n    return y\n")
    assert any(edge.type is CFGEdgeType.NO_MATCH for edge in built.cfg.edges)


def test_nested_function_body_is_not_descended():
    built = _build(
        "def outer():\n    def inner():\n        return 1\n    return inner()\n",
        name="outer",
    )
    assert [node.kind for node in built.cfg.nodes] == [
        CFGNodeKind.ENTRY,
        CFGNodeKind.EXIT,
        CFGNodeKind.NESTED_DEF,
        CFGNodeKind.RETURN,
    ]
    assert all(node.line != 3 for node in built.cfg.nodes)
    assert sum(1 for node in built.cfg.nodes if node.kind is CFGNodeKind.RETURN) == 1


def test_post_termination_statements_are_omitted_with_note():
    built = _build("def f(a):\n    return a\n    x = 1\n")
    assert [node.kind for node in built.cfg.nodes] == [
        CFGNodeKind.ENTRY,
        CFGNodeKind.EXIT,
        CFGNodeKind.RETURN,
    ]
    assert any("unreachable" in note for note in built.cfg.notes)


def test_raise_terminates_path_and_targets_exit():
    built = _build("def f(a):\n    if a:\n        raise ValueError()\n    return 1\n")
    triples = _triples(built)
    assert ("n2", "n3", CFGEdgeType.TRUE) in triples
    assert ("n3", "n1", CFGEdgeType.RAISE) in triples
    assert not any(
        edge.source == "n3" and edge.type is CFGEdgeType.NORMAL for edge in built.cfg.edges
    )


def test_pass_body_is_a_statement_node():
    built = _build("def f():\n    pass\n")
    assert [node.kind for node in built.cfg.nodes] == [
        CFGNodeKind.ENTRY,
        CFGNodeKind.EXIT,
        CFGNodeKind.STATEMENT,
    ]
    assert _triples(built) == [
        ("n0", "n2", CFGEdgeType.NORMAL),
        ("n2", "n1", CFGEdgeType.NORMAL),
    ]


def test_async_for_and_async_with_are_modelled():
    built = _build(
        "async def f(xs, lock):\n"
        "    async for x in xs:\n"
        "        async with lock:\n"
        "            pass\n"
        "    return x\n"
    )
    kinds = {node.kind for node in built.cfg.nodes}
    assert CFGNodeKind.LOOP in kinds
    assert CFGNodeKind.WITH in kinds
    assert CFGNodeKind.RETURN in kinds


def test_break_in_loop_else_targets_the_loop_exit():
    built = _build(
        "def f(xs):\n    for x in xs:\n        pass\n    else:\n        break\n    return 1\n"
    )
    exits = [node for node in built.cfg.nodes if node.label == "loop-exit"]
    assert len(exits) == 1
    assert any(
        edge.target == exits[0].id and edge.type is CFGEdgeType.BREAK for edge in built.cfg.edges
    )
    assert not any("break outside" in note for note in built.cfg.notes)


def test_terminal_loop_else_does_not_leave_an_orphan_merge():
    built = _build(
        "def f(xs):\n    for x in xs:\n        pass\n    else:\n        return 1\n    return 2\n"
    )
    assert all(node.kind is not CFGNodeKind.MERGE for node in built.cfg.nodes)
    assert not any(
        node.line == 6 and node.kind not in {CFGNodeKind.ENTRY, CFGNodeKind.EXIT}
        for node in built.cfg.nodes
    )
    node_ids = {node.id for node in built.cfg.nodes}
    assert all(edge.source in node_ids and edge.target in node_ids for edge in built.cfg.edges)
    assert any("unreachable statements" in note for note in built.cfg.notes)


def test_terminal_try_finally_does_not_leave_orphan_nodes():
    built = _build(
        "def f():\n    try:\n        return 1\n    finally:\n        y = 2\n    return 3\n"
    )
    assert all(node.label != "finally" for node in built.cfg.nodes)
    assert all(node.kind is not CFGNodeKind.MERGE for node in built.cfg.nodes)
    assert not any(
        node.line in {5, 6} and node.kind not in {CFGNodeKind.ENTRY, CFGNodeKind.EXIT}
        for node in built.cfg.nodes
    )
    node_ids = {node.id for node in built.cfg.nodes}
    assert all(edge.source in node_ids and edge.target in node_ids for edge in built.cfg.edges)
    assert any("try/finally body omitted" in note for note in built.cfg.notes)


def test_bounded_cfg_reports_truncation():
    body = "".join(f"    x{i} = {i}\n" for i in range(20))
    built = _build("def f():\n" + body + "    return 0\n", max_nodes=5, max_edges=10)
    assert built.cfg.truncated is True
    assert len(built.cfg.nodes) == 5
    assert any("node cap" in note for note in built.cfg.notes)


def test_guarded_wildcard_is_not_exhaustive():
    built = _build(
        "def f(x, cond):\n"
        "    match x:\n"
        "        case _ if cond:\n"
        "            return 1\n"
        "    return 2\n"
    )
    assert any(edge.type is CFGEdgeType.NO_MATCH for edge in built.cfg.edges)
    assert any(node.kind is CFGNodeKind.RETURN and node.line == 5 for node in built.cfg.nodes)
    node_ids = {node.id for node in built.cfg.nodes}
    assert all(edge.source in node_ids and edge.target in node_ids for edge in built.cfg.edges)


def test_exhaustive_terminal_match_has_no_orphan_merge():
    built = _build(
        "def f(x):\n"
        "    match x:\n"
        "        case 1:\n"
        "            return 1\n"
        "        case _:\n"
        "            return 2\n"
    )
    assert all(node.kind is not CFGNodeKind.MERGE for node in built.cfg.nodes)
    assert sum(node.kind is CFGNodeKind.RETURN for node in built.cfg.nodes) == 2
    node_ids = {node.id for node in built.cfg.nodes}
    assert all(edge.source in node_ids and edge.target in node_ids for edge in built.cfg.edges)


def test_try_exception_edges_skip_removed_nested_merge():
    built = _build(
        "def f(x):\n"
        "    try:\n"
        "        match x:\n"
        "            case 1:\n"
        "                return 1\n"
        "            case _:\n"
        "                return 2\n"
        "    except Exception:\n"
        "        pass\n"
        "    return 3\n"
    )
    node_ids = {node.id for node in built.cfg.nodes}
    assert all(edge.source in node_ids and edge.target in node_ids for edge in built.cfg.edges)
    handler = next(node for node in built.cfg.nodes if node.kind is CFGNodeKind.HANDLER)
    assert any(
        edge.target == handler.id and edge.type is CFGEdgeType.EXCEPTION for edge in built.cfg.edges
    )
