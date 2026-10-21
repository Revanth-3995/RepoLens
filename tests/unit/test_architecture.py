"""P2-07: exact cycle, centrality, coupling, hotspot and finding tests."""

from __future__ import annotations

from repolens.analysis.architecture import analyze_architecture
from repolens.core.models import (
    EntryPoint,
    FileRecord,
    FindingSeverity,
    FunctionMetricsRecord,
    ParseStatus,
    RelationshipRecord,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
    SymbolRecord,
)


def _file(rel_path: str, file_id: str) -> FileRecord:
    return FileRecord(
        id=file_id,
        repository_id="repo_1",
        rel_path=rel_path,
        module_name=rel_path.removesuffix(".py").replace("/", "."),
        size_bytes=10,
        content_hash=f"hash-{file_id}",
        parse_status=ParseStatus.PARSED,
    )


def _module(name: str, file_id: str, rel_path: str) -> SymbolRecord:
    return SymbolRecord(
        id=f"m_{name}",
        repository_id="repo_1",
        file_id=file_id,
        rel_path=rel_path,
        name=name,
        qualified_name=name,
        kind=SymbolKind.MODULE,
        start_line=1,
        end_line=10,
    )


def _import(
    rel_id: str, source_file: str, target: SymbolRecord, *, line: int = 1, source_path: str
) -> RelationshipRecord:
    return RelationshipRecord(
        id=rel_id,
        repository_id="repo_1",
        source_file_id=source_file,
        source_symbol_id="src",
        source_rel_path=source_path,
        source_line=line,
        source_col=0,
        rel_type=RelationshipType.IMPORTS,
        target_symbol_id=target.id,
        target_ref=target.qualified_name,
        resolution_status=ResolutionStatus.RESOLVED_STATIC,
        evidence=f"import at {source_path}:{line}",
    )


def _metrics(
    name: str,
    *,
    rel_path: str,
    file_id: str,
    complexity: int,
    lines: int,
    nesting: int = 0,
    start_line: int = 1,
    symbol_id: str = "",
) -> FunctionMetricsRecord:
    return FunctionMetricsRecord(
        symbol_id=symbol_id,
        file_id=file_id,
        rel_path=rel_path,
        qualified_name=name,
        start_line=start_line,
        end_line=start_line + lines - 1,
        line_count=lines,
        cyclomatic_complexity=complexity,
        max_nesting_depth=nesting,
    )


def _triangle() -> tuple[list[FileRecord], list[SymbolRecord], list[RelationshipRecord]]:
    files = [_file("a.py", "f_a"), _file("b.py", "f_b"), _file("c.py", "f_c")]
    m_a = _module("a", "f_a", "a.py")
    m_b = _module("b", "f_b", "b.py")
    m_c = _module("c", "f_c", "c.py")
    relationships = [
        _import("r_ab", "f_a", m_b, line=2, source_path="a.py"),
        _import("r_ba", "f_b", m_a, line=3, source_path="b.py"),
        _import("r_ac", "f_a", m_c, line=4, source_path="a.py"),
    ]
    return files, [m_a, m_b, m_c], relationships


def _summary(**kwargs):
    files, symbols, relationships = _triangle()
    metrics = [
        _metrics(
            "a.big",
            rel_path="a.py",
            file_id="f_a",
            complexity=12,
            lines=50,
            nesting=4,
            start_line=5,
            symbol_id="s_big",
        ),
        _metrics("b.small", rel_path="b.py", file_id="f_b", complexity=2, lines=5),
    ]
    entry_points = [
        EntryPoint(rel_path="a.py", line=1, evidence='if __name__ == "__main__"'),
    ]
    return analyze_architecture(
        "repo_1",
        files,
        symbols,
        relationships,
        metrics,
        entry_points,
        **kwargs,
    )


def test_cycle_centrality_coupling_and_thresholds_are_exact():
    summary = _summary()
    assert summary.files == 3
    assert summary.dependency_edges == 3
    assert len(summary.cycles) == 1
    cycle = summary.cycles[0]
    assert cycle.files == ["a.py", "b.py", "a.py"]
    assert [(edge.source_rel_path, edge.target_rel_path, edge.line) for edge in cycle.edges] == [
        ("a.py", "b.py", 2),
        ("b.py", "a.py", 3),
    ]

    assert [
        (
            component.rel_path,
            component.in_degree,
            component.out_degree,
            component.degree,
            component.centrality,
        )
        for component in summary.central_components
    ] == [
        ("a.py", 1, 2, 3, 1.5),
        ("b.py", 1, 1, 2, 1.0),
        ("c.py", 1, 0, 1, 0.5),
    ]
    assert [
        (metric.rel_path, metric.fan_in, metric.fan_out, metric.instability)
        for metric in summary.coupling
    ] == [
        ("a.py", 1, 2, 0.666667),
        ("b.py", 1, 1, 0.5),
        ("c.py", 1, 0, 0.0),
    ]
    assert summary.disconnected_components == 1
    assert summary.thresholds["complexity_warning"] == 10
    assert summary.thresholds["fan_out_warning"] == 10
    assert summary.thresholds["fan_in_warning"] == 10
    assert "degree centrality" in str(summary.thresholds["centrality"])
    assert "fan_out/(fan_in+fan_out)" in str(summary.thresholds["instability"])
    assert summary.entry_points == [
        EntryPoint(rel_path="a.py", line=1, evidence='if __name__ == "__main__"')
    ]


def test_hotspots_and_findings_are_ordered_and_labelled():
    summary = _summary()
    assert [
        (hotspot.qualified_name, hotspot.cyclomatic_complexity, hotspot.symbol_id)
        for hotspot in summary.complexity_hotspots
    ] == [("a.big", 12, "s_big"), ("b.small", 2, None)]

    assert [finding.kind for finding in summary.findings] == [
        "complexity_hotspot",
        "dependency_cycle",
        "central_component",
    ]
    complexity, cycle, central = summary.findings
    assert complexity.severity is FindingSeverity.WARNING
    assert complexity.heuristic is True
    assert complexity.metrics == {
        "cyclomatic_complexity": 12,
        "line_count": 50,
        "max_nesting_depth": 4,
        "threshold": 10,
    }
    assert complexity.evidence[0].rel_path == "a.py"
    assert complexity.evidence[0].line == 5
    assert cycle.severity is FindingSeverity.WARNING
    assert cycle.heuristic is False
    assert "not that the code is wrong" in cycle.description
    assert cycle.metrics == {"length": 2}
    assert central.severity is FindingSeverity.INFO
    assert central.heuristic is True
    assert central.subject == "a.py"
    assert central.metrics["centrality"] == 1.5
    assert central.metrics["fan_in"] == 1
    assert central.metrics["fan_out"] == 2


def test_fan_in_fan_out_disconnected_and_finding_caps():
    files, symbols, relationships = _triangle()
    files.append(_file("d.py", "f_d"))
    symbols.append(_module("d", "f_d", "d.py"))
    metrics = [
        _metrics(
            "a.big", rel_path="a.py", file_id="f_a", complexity=12, lines=50, symbol_id="s_big"
        )
    ]
    summary = analyze_architecture(
        "repo_1",
        files,
        symbols,
        relationships,
        metrics,
        [],
        fan_out_warning=2,
        fan_in_warning=1,
        max_findings=2,
    )
    kinds = [finding.kind for finding in summary.findings]
    assert kinds == ["complexity_hotspot", "dependency_cycle"]
    assert summary.truncated is True
    assert any("findings truncated at max_findings=2" in note for note in summary.notes)

    full = analyze_architecture(
        "repo_1",
        files,
        symbols,
        relationships,
        metrics,
        [],
        fan_out_warning=2,
        fan_in_warning=1,
        max_findings=100,
    )
    kinds_full = [finding.kind for finding in full.findings]
    assert kinds_full.count("high_fan_out") == 1
    assert kinds_full.count("high_fan_in") == 3
    assert "disconnected_components" in kinds_full
    disconnected = next(f for f in full.findings if f.kind == "disconnected_components")
    assert disconnected.severity is FindingSeverity.INFO
    assert disconnected.heuristic is False
    assert disconnected.metrics == {"components": 2}


def test_same_file_imports_and_duplicate_edges_are_collapsed():
    files, symbols, relationships = _triangle()
    m_a = symbols[0]
    m_b = symbols[1]
    relationships = [
        _import("r_self", "f_a", m_a, line=1, source_path="a.py"),
        _import("r_ab_1", "f_a", m_b, line=5, source_path="a.py"),
        _import("r_ab_2", "f_a", m_b, line=2, source_path="a.py"),
    ]
    summary = analyze_architecture("repo_1", files, symbols, relationships, [], [])
    assert summary.dependency_edges == 1
    assert summary.cycles == []
    coupling = {metric.rel_path: metric for metric in summary.coupling}
    assert coupling["a.py"].fan_out == 1
    assert coupling["b.py"].fan_in == 1


def test_cycle_enumeration_cap_is_reported():
    files = [
        _file("a.py", "f_a"),
        _file("b.py", "f_b"),
        _file("c.py", "f_c"),
        _file("d.py", "f_d"),
    ]
    m_a = _module("a", "f_a", "a.py")
    m_b = _module("b", "f_b", "b.py")
    m_c = _module("c", "f_c", "c.py")
    m_d = _module("d", "f_d", "d.py")
    relationships = [
        _import("r_ab", "f_a", m_b, source_path="a.py"),
        _import("r_ba", "f_b", m_a, source_path="b.py"),
        _import("r_cd", "f_c", m_d, source_path="c.py"),
        _import("r_dc", "f_d", m_c, source_path="d.py"),
    ]
    summary = analyze_architecture(
        "repo_1", files, [m_a, m_b, m_c, m_d], relationships, [], [], max_cycles=1
    )
    assert len(summary.cycles) == 1
    assert summary.truncated is True
    assert any("max_cycles=1" in note for note in summary.notes)


def test_empty_repository_is_well_defined():
    summary = analyze_architecture("repo_1", [], [], [], [], [])
    assert summary.files == 0
    assert summary.dependency_edges == 0
    assert summary.cycles == []
    assert summary.central_components == []
    assert summary.coupling == []
    assert summary.complexity_hotspots == []
    assert summary.findings == []
    assert summary.disconnected_components == 0


def test_single_file_has_zero_centrality_and_no_findings():
    file = _file("only.py", "f_only")
    summary = analyze_architecture("repo_1", [file], [], [], [], [])
    assert summary.disconnected_components == 1
    assert summary.central_components[0].centrality == 0.0
    assert summary.findings == []
