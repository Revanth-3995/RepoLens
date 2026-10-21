"""P2-06: bounded forward/reverse/both tracing, connect, file dependents."""

from __future__ import annotations

from repolens.analysis.tracing import (
    connect_symbols,
    trace_file_dependents,
    trace_symbol,
)
from repolens.core.models import (
    Direction,
    RelationshipRecord,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
    SymbolRecord,
)


def _sym(
    symbol_id: str,
    name: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    file_id: str = "f_a",
    rel_path: str = "a.py",
    start_line: int = 1,
) -> SymbolRecord:
    return SymbolRecord(
        id=symbol_id,
        repository_id="repo_1",
        file_id=file_id,
        rel_path=rel_path,
        name=name,
        qualified_name=name,
        kind=kind,
        start_line=start_line,
        end_line=start_line + 5,
    )


def _rel(
    rel_id: str,
    source: SymbolRecord,
    target: SymbolRecord | None,
    *,
    rel_type: RelationshipType = RelationshipType.CALLS,
    status: ResolutionStatus = ResolutionStatus.RESOLVED_STATIC,
    rel_path: str | None = None,
    line: int = 1,
    target_ref: str = "target",
) -> RelationshipRecord:
    return RelationshipRecord(
        id=rel_id,
        repository_id="repo_1",
        source_file_id=source.file_id,
        source_symbol_id=source.id,
        source_rel_path=rel_path or source.rel_path,
        source_line=line,
        source_col=0,
        rel_type=rel_type,
        target_symbol_id=target.id if target is not None else None,
        target_ref=target_ref,
        resolution_status=status,
        evidence=f"{rel_type.value} evidence at {rel_path or source.rel_path}:{line}",
    )


def _graph() -> tuple[list[SymbolRecord], list[RelationshipRecord]]:
    a = _sym("s_a", "a.func", rel_path="a.py")
    b = _sym("s_b", "b.func", rel_path="b.py")
    c = _sym("s_c", "c.func", rel_path="c.py")
    d = _sym("s_d", "d.func", rel_path="d.py")
    relationships = [
        _rel("r_ab", a, b, rel_path="a.py", line=2),
        _rel("r_bc", b, c, rel_path="b.py", line=3),
        _rel("r_ca", c, a, rel_path="c.py", line=4),
        _rel(
            "r_ext",
            a,
            None,
            status=ResolutionStatus.EXTERNAL,
            rel_path="a.py",
            line=5,
            target_ref="external_lib.call",
        ),
        _rel(
            "r_amb",
            a,
            None,
            status=ResolutionStatus.AMBIGUOUS,
            rel_path="a.py",
            line=6,
            target_ref="ambiguous.call",
        ),
    ]
    return [a, b, c, d], relationships


def test_forward_trace_has_nodes_edges_unresolved_and_cycle():
    symbols, relationships = _graph()
    result = trace_symbol("repo_1", symbols[0], symbols, relationships, direction=Direction.OUT)
    assert [(node.symbol_id, node.depth) for node in result.nodes] == [
        ("s_a", 0),
        ("s_b", 1),
        ("s_c", 2),
    ]
    assert [
        (edge.source_symbol_id, edge.target_symbol_id, edge.rel_type) for edge in result.edges
    ] == [
        ("s_a", "s_b", RelationshipType.CALLS),
        ("s_b", "s_c", RelationshipType.CALLS),
        ("s_c", "s_a", RelationshipType.CALLS),
    ]
    assert [edge.target_ref for edge in result.unresolved] == [
        "external_lib.call",
        "ambiguous.call",
    ]
    assert {edge.resolution_status for edge in result.unresolved} == {
        ResolutionStatus.EXTERNAL,
        ResolutionStatus.AMBIGUOUS,
    }
    assert [edge.target_ref for edge in result.nodes[0].unresolved_references] == [
        "external_lib.call",
        "ambiguous.call",
    ]
    assert result.cycles == [["s_a", "s_b", "s_c", "s_a"]]
    assert result.truncated is False
    assert any("not a runtime execution path" in note for note in result.notes)


def test_reverse_trace_follows_stored_edge_direction():
    symbols, relationships = _graph()
    result = trace_symbol("repo_1", symbols[2], symbols, relationships, direction=Direction.IN)
    assert [(node.symbol_id, node.depth) for node in result.nodes] == [
        ("s_c", 0),
        ("s_b", 1),
        ("s_a", 2),
    ]
    # Edges keep their stored direction even when traversed backwards.
    assert [(edge.source_symbol_id, edge.target_symbol_id) for edge in result.edges] == [
        ("s_b", "s_c"),
        ("s_a", "s_b"),
        ("s_c", "s_a"),
    ]
    assert result.cycles == [["s_c", "s_b", "s_a", "s_c"]]
    assert any("reverse edges" in note for note in result.notes)


def test_both_trace_does_not_invent_backtrack_cycles():
    symbols, relationships = _graph()
    result = trace_symbol("repo_1", symbols[0], symbols, relationships, direction=Direction.BOTH)
    assert [node.symbol_id for node in result.nodes] == ["s_a", "s_b", "s_c"]
    assert result.cycles == []
    assert any("architecture cycles remain authoritative" in note for note in result.notes)


def test_depth_and_node_bounds_are_enforced_and_reported():
    symbols, relationships = _graph()
    shallow = trace_symbol(
        "repo_1", symbols[0], symbols, relationships, direction=Direction.OUT, max_depth=1
    )
    assert [node.symbol_id for node in shallow.nodes] == ["s_a", "s_b"]
    assert shallow.max_depth == 1
    assert shallow.truncated is True
    assert any("max_depth cap" in note for note in shallow.notes)

    capped = trace_symbol(
        "repo_1", symbols[0], symbols, relationships, direction=Direction.OUT, max_nodes=2
    )
    assert [node.symbol_id for node in capped.nodes] == ["s_a", "s_b"]
    assert capped.truncated is True
    assert any("visited-node cap" in note for note in capped.notes)

    root_only = trace_symbol(
        "repo_1", symbols[0], symbols, relationships, direction=Direction.OUT, max_nodes=1
    )
    assert [node.symbol_id for node in root_only.nodes] == ["s_a"]
    assert root_only.edges == []
    assert root_only.truncated is True


def test_unresolved_references_at_the_depth_boundary_are_still_reported():
    a = _sym("s_a", "a.func", rel_path="a.py")
    b = _sym("s_b", "b.func", rel_path="b.py")
    relationships = [
        _rel("r_ab", a, b, line=2),
        _rel(
            "r_b_ext",
            b,
            None,
            status=ResolutionStatus.UNRESOLVED,
            rel_path="b.py",
            line=3,
            target_ref="mystery",
        ),
    ]
    result = trace_symbol("repo_1", a, [a, b], relationships, direction=Direction.OUT, max_depth=1)
    assert [edge.target_ref for edge in result.unresolved] == ["mystery"]
    assert [node.symbol_id for node in result.nodes] == ["s_a", "s_b"]


def test_non_resolved_relationship_with_target_is_reported_not_traversed():
    a = _sym("s_a", "a.func", rel_path="a.py")
    b = _sym("s_b", "b.func", rel_path="b.py")
    relationships = [
        _rel(
            "r_ab",
            a,
            b,
            status=ResolutionStatus.AMBIGUOUS,
            rel_path="a.py",
            line=2,
        )
    ]
    result = trace_symbol("repo_1", a, [a, b], relationships, direction=Direction.OUT)
    assert [node.symbol_id for node in result.nodes] == ["s_a"]
    assert result.edges == []
    assert [edge.target_symbol_id for edge in result.unresolved] == ["s_b"]


def test_resolved_relationship_with_missing_target_symbol_is_reported_not_traversed():
    a = _sym("s_a", "a.func", rel_path="a.py")
    b = _sym("s_b", "b.func", rel_path="b.py")
    relationships = [_rel("r_ab", a, b, rel_path="a.py", line=2)]
    result = trace_symbol("repo_1", a, [a], relationships, direction=Direction.OUT)
    assert [node.symbol_id for node in result.nodes] == ["s_a"]
    assert result.edges == []
    assert [edge.target_symbol_id for edge in result.unresolved] == ["s_b"]


def test_relationship_type_filter_restricts_traversal():
    a = _sym("s_a", "a.func", rel_path="a.py")
    b = _sym("s_b", "b.func", rel_path="b.py")
    c = _sym("s_c", "c.func", rel_path="c.py")
    relationships = [
        _rel("r_ab", a, b, rel_type=RelationshipType.IMPORTS, line=1),
        _rel("r_bc", b, c, rel_type=RelationshipType.CALLS, line=2),
    ]
    imports_only = trace_symbol(
        "repo_1",
        a,
        [a, b, c],
        relationships,
        direction=Direction.OUT,
        rel_types={RelationshipType.IMPORTS},
    )
    assert [node.symbol_id for node in imports_only.nodes] == ["s_a", "s_b"]
    assert [edge.rel_type for edge in imports_only.edges] == [RelationshipType.IMPORTS]


def test_connect_finds_a_bounded_path_and_reports_missing_paths():
    symbols, relationships = _graph()
    a, _b, c, d = symbols
    found = connect_symbols("repo_1", a, c, symbols, relationships, direction=Direction.OUT)
    assert found.found is True
    assert found.nodes == ["s_a", "s_b", "s_c"]
    assert found.length == 2
    assert [(edge.source_symbol_id, edge.target_symbol_id) for edge in found.edges] == [
        ("s_a", "s_b"),
        ("s_b", "s_c"),
    ]

    missing = connect_symbols("repo_1", a, d, symbols, relationships, direction=Direction.OUT)
    assert missing.found is False
    assert "no static relationship path" in (missing.reason or "")

    too_deep = connect_symbols(
        "repo_1", a, c, symbols, relationships, direction=Direction.OUT, max_depth=1
    )
    assert too_deep.found is False
    assert "max_depth=1" in (too_deep.reason or "")
    assert too_deep.truncated is True


def test_connect_self_is_trivial_and_caps_are_reported():
    symbols, relationships = _graph()
    a, _b, c, _d = symbols
    same = connect_symbols("repo_1", a, a, symbols, relationships)
    assert same.found is True
    assert same.nodes == ["s_a"]
    assert same.length == 0

    capped = connect_symbols(
        "repo_1", a, c, symbols, relationships, direction=Direction.OUT, max_nodes=2
    )
    assert capped.found is False
    assert capped.truncated is True
    assert capped.reason == "visited-node cap reached"


def test_file_dependents_maps_import_targets_to_their_file():
    m_a = _sym("m_a", "a", kind=SymbolKind.MODULE, file_id="f_a", rel_path="a.py")
    m_b = _sym("m_b", "b", kind=SymbolKind.MODULE, file_id="f_b", rel_path="b.py")
    m_c = _sym("m_c", "c", kind=SymbolKind.MODULE, file_id="f_c", rel_path="c.py")
    b_helper = _sym("b_helper", "b.helper", file_id="f_b", rel_path="b.py")
    c_thing = _sym("c_thing", "c.thing", file_id="f_c", rel_path="c.py")
    a_main = _sym("a_main", "a.main", file_id="f_a", rel_path="a.py")
    relationships = [
        _rel(
            "r_ab",
            m_a,
            b_helper,
            rel_type=RelationshipType.IMPORTS,
            rel_path="a.py",
            line=1,
            target_ref="b.helper",
        ),
        _rel(
            "r_bc",
            m_b,
            c_thing,
            rel_type=RelationshipType.IMPORTS,
            rel_path="b.py",
            line=1,
            target_ref="c.thing",
        ),
        _rel(
            "r_ca",
            m_c,
            a_main,
            rel_type=RelationshipType.IMPORTS,
            rel_path="c.py",
            line=1,
            target_ref="a.main",
        ),
    ]
    result = trace_file_dependents(
        "repo_1", m_c, [m_a, m_b, m_c, b_helper, c_thing, a_main], relationships
    )
    assert [(node.symbol_id, node.depth) for node in result.nodes] == [
        ("m_c", 0),
        ("m_b", 1),
        ("m_a", 2),
    ]
    assert [(edge.source_symbol_id, edge.target_symbol_id) for edge in result.edges] == [
        ("m_b", "c_thing"),
        ("m_a", "b_helper"),
        ("m_c", "a_main"),
    ]
    assert result.cycles == [["m_c", "m_b", "m_a", "m_c"]]
    assert any("file granularity" in note for note in result.notes)


def test_file_dependents_respects_depth_and_has_no_symbol_unresolved_edges():
    m_a = _sym("m_a", "a", kind=SymbolKind.MODULE, file_id="f_a", rel_path="a.py")
    m_b = _sym("m_b", "b", kind=SymbolKind.MODULE, file_id="f_b", rel_path="b.py")
    b_helper = _sym("b_helper", "b.helper", file_id="f_b", rel_path="b.py")
    relationships = [
        _rel(
            "r_ab",
            m_a,
            b_helper,
            rel_type=RelationshipType.IMPORTS,
            rel_path="a.py",
            line=1,
            target_ref="b.helper",
        ),
        _rel(
            "r_ext",
            m_a,
            None,
            rel_type=RelationshipType.IMPORTS,
            status=ResolutionStatus.EXTERNAL,
            rel_path="a.py",
            line=2,
            target_ref="external",
        ),
    ]
    result = trace_file_dependents("repo_1", m_b, [m_a, m_b, b_helper], relationships, max_depth=1)
    assert [node.symbol_id for node in result.nodes] == ["m_b", "m_a"]
    assert result.unresolved == []
    assert result.max_depth == 1

    root_only = trace_file_dependents(
        "repo_1", m_b, [m_a, m_b, b_helper], relationships, max_nodes=1
    )
    assert [node.symbol_id for node in root_only.nodes] == ["m_b"]
    assert root_only.edges == []
    assert root_only.truncated is True
