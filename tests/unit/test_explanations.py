"""P2-04: explanations separate facts, heuristics, unknowns and evidence."""

from __future__ import annotations

import ast

from repolens.analysis.cfg import CFGWithFacts, build_function_cfg_with_facts
from repolens.analysis.dataflow import analyze_dataflow
from repolens.analysis.explanations import (
    explain_file,
    explain_folder,
    explain_function,
    explain_project,
    explain_statement,
)
from repolens.analysis.symbols import extract_symbols
from repolens.core.models import (
    ArchitectureFinding,
    ArchitectureSummary,
    CentralComponent,
    ComplexityHotspot,
    DependencyCycle,
    DependencyEdge,
    EntryPoint,
    EvidenceRef,
    ExplanationLevel,
    FileDetail,
    FileRecord,
    FindingSeverity,
    FunctionMetricsRecord,
    ParameterModel,
    ParseStatus,
    RelationshipRecord,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
    SymbolRecord,
)


def _symbol(
    symbol_id: str,
    name: str,
    qualified_name: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    file_id: str = "f_a",
    rel_path: str = "m.py",
    start_line: int = 1,
    end_line: int = 10,
    parent_id: str | None = None,
    signature: str | None = None,
    docstring: str | None = None,
    parameters: list[ParameterModel] | None = None,
) -> SymbolRecord:
    return SymbolRecord(
        id=symbol_id,
        repository_id="repo_1",
        file_id=file_id,
        rel_path=rel_path,
        name=name,
        qualified_name=qualified_name,
        kind=kind,
        parent_id=parent_id,
        signature=signature,
        docstring=docstring,
        parameters=parameters or [],
        start_line=start_line,
        end_line=end_line,
    )


def _file(
    rel_path: str = "m.py",
    file_id: str = "f_a",
    module_name: str = "m",
    *,
    detail: bool = False,
) -> FileRecord | FileDetail:
    values = {
        "id": file_id,
        "repository_id": "repo_1",
        "rel_path": rel_path,
        "module_name": module_name,
        "size_bytes": 100,
        "content_hash": "hash",
        "parse_status": ParseStatus.PARSED,
    }
    if detail:
        return FileDetail(**values, source_available=True, content_changed_since_analysis=False)
    return FileRecord(**values)


def _build(source: str, name: str = "f") -> tuple[CFGWithFacts, SymbolRecord]:
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
        symbol_id="s_f",
        qualified_name=f"m.{name}",
        file_id="f_a",
        rel_path="m.py",
    )
    symbol = _symbol(
        "s_f",
        name,
        f"m.{name}",
        start_line=node.lineno,
        end_line=getattr(node, "end_lineno", node.lineno),
        signature=f"def {name}",
        parameters=[ParameterModel(name="a", kind="arg")],
    )
    return built, symbol


def _metrics(complexity: int = 2) -> FunctionMetricsRecord:
    return FunctionMetricsRecord(
        symbol_id="s_f",
        file_id="f_a",
        rel_path="m.py",
        qualified_name="m.f",
        start_line=1,
        end_line=4,
        parameter_count=1,
        line_count=4,
        statement_count=3,
        if_count=1,
        return_count=2,
        cyclomatic_complexity=complexity,
        max_nesting_depth=1,
    )


def _call(
    rel_id: str,
    source_id: str,
    target_id: str | None,
    *,
    source_path: str = "m.py",
    target_ref: str = "m.helper",
    line: int = 3,
    status: ResolutionStatus = ResolutionStatus.RESOLVED_STATIC,
) -> RelationshipRecord:
    return RelationshipRecord(
        id=rel_id,
        repository_id="repo_1",
        source_file_id="f_a",
        source_symbol_id=source_id,
        source_rel_path=source_path,
        source_line=line,
        source_col=0,
        rel_type=RelationshipType.CALLS,
        target_symbol_id=target_id,
        target_ref=target_ref,
        call_expr=f"{target_ref}(...)",
        resolution_status=status,
        evidence=f"call at {source_path}:{line}",
    )


def test_function_explanation_has_facts_heuristics_calls_and_evidence():
    built, symbol = _build("def f(a):\n    if a:\n        return 1\n    return 2\n")
    dataflow = analyze_dataflow(built)
    relationships = [
        _call("r_out", "s_f", "s_helper", target_ref="m.helper"),
        _call("r_in", "s_caller", "s_f", source_path="caller.py", target_ref="m.f", line=9),
        _call(
            "r_unresolved",
            "s_f",
            None,
            target_ref="dynamic.call",
            line=4,
            status=ResolutionStatus.DYNAMIC_OR_UNKNOWN,
        ),
    ]
    explanation = explain_function(
        symbol, _metrics(), built.cfg, dataflow, relationships, complexity_threshold=10
    )
    assert explanation.level is ExplanationLevel.FUNCTION
    assert explanation.subject == "m.f"
    assert any("`m.f`" in fact and "m.py" in fact for fact in explanation.facts)
    assert "Signature: `def f`." in explanation.facts
    assert "Parameters: a." in explanation.facts
    assert any(
        "Metrics: 4 lines, 3 statement(s), 2 return(s)" in fact for fact in explanation.facts
    )
    assert any("Control-flow graph:" in fact for fact in explanation.facts)
    assert any("Control-flow constructs: 1" in fact for fact in explanation.facts)
    assert any("[condition]" in fact and "line 2" in fact for fact in explanation.facts)
    assert any("Local data flow:" in fact for fact in explanation.facts)
    assert "Outgoing calls: 2 reference(s), 1 resolved statically." in explanation.facts
    assert any(
        "dynamic.call" in fact and "dynamic_or_unknown" in fact for fact in explanation.facts
    )
    assert "Incoming calls: 1 resolved call reference(s) from other symbols." in explanation.facts
    assert any("review threshold 10" in heuristic for heuristic in explanation.heuristics)
    assert not any("review threshold 10" in fact for fact in explanation.facts)
    assert any(
        "lambda bodies and comprehension interiors" in unknown for unknown in explanation.unknowns
    )
    evidence_kinds = {item.kind for item in explanation.evidence}
    assert {"symbol", "metric", "cfg", "relationship"} <= evidence_kinds
    assert explanation.evidence[0].symbol_id == "s_f"
    assert explanation.evidence[0].line == 1
    assert [section.title for section in explanation.sections] == [
        "Signature",
        "Structural metrics",
        "Control flow",
    ]
    assert any("condition:" in line for line in explanation.sections[-1].lines)


def test_function_explanation_states_missing_metrics_and_truncation():
    built, symbol = _build("def f(a):\n    return a\n")
    truncated = build_function_cfg_with_facts(
        next(
            n
            for n in ast.walk(ast.parse("def f(a):\n    return a\n"))
            if isinstance(n, ast.FunctionDef)
        ),
        ["def f(a):", "    return a"],
        max_nodes=1,
    )
    explanation = explain_function(
        symbol, None, truncated.cfg, analyze_dataflow(built), [], complexity_threshold=10
    )
    assert "No persisted metrics are available for this function symbol." in explanation.unknowns
    assert any("resource cap" in unknown for unknown in explanation.unknowns)
    assert not any(fact.startswith("Metrics:") for fact in explanation.facts)


def test_statement_explanation_prefers_the_innermost_node_and_cites_edges():
    built, symbol = _build("def f(a):\n    if a:\n        return 1\n    return 2\n")
    explanation = explain_statement(symbol, built.cfg, analyze_dataflow(built), 2)
    assert explanation.level is ExplanationLevel.STATEMENT
    assert explanation.subject == "m.f:2"
    assert any("is a `condition` statement at m.py:2" in fact for fact in explanation.facts)
    assert any("Incoming edges: `n0` → `n2` (normal)" in fact for fact in explanation.facts)
    assert any("(true)" in fact and "(false)" in fact for fact in explanation.facts)
    assert any("control-flow decision" in heuristic for heuristic in explanation.heuristics)
    assert explanation.sections[0].title == "Source"
    assert explanation.sections[0].lines == ["if a: return 1"]
    assert explanation.evidence[-1].kind == "cfg"
    assert explanation.evidence[-1].line == 2
    assert explanation.unknowns == []


def test_statement_explanation_reports_local_definitions_and_reaching_defs():
    built, symbol = _build("def f():\n    x = 1\n    y = x + 1\n    return y\n")
    explanation = explain_statement(symbol, built.cfg, analyze_dataflow(built), 3)
    assert any(
        "Local definitions here: `y` (assignment, d1)." in fact for fact in explanation.facts
    )
    assert any("Local reads here: `x` → d0." in fact for fact in explanation.facts)


def test_statement_explanation_for_a_line_with_no_cfg_node():
    built, symbol = _build("def f():\n\n    return 1\n")
    explanation = explain_statement(symbol, built.cfg, analyze_dataflow(built), 2)
    assert explanation.facts == []
    assert any("No CFG statement node covers line 2" in unknown for unknown in explanation.unknowns)
    assert explanation.level is ExplanationLevel.STATEMENT


def test_file_explanation_summarises_symbols_dependencies_and_entry_points():
    file = _file(detail=True)
    module = _symbol(
        "s_m", "m", "m", kind=SymbolKind.MODULE, start_line=1, end_line=10, docstring="Module docs."
    )
    function = _symbol("s_f", "f", "m.f", parent_id="s_m", start_line=2, end_line=4)
    target = _symbol(
        "s_t", "t", "other.t", file_id="f_b", rel_path="other.py", start_line=1, end_line=3
    )
    relationships = [
        RelationshipRecord(
            id="r_ext",
            repository_id="repo_1",
            source_file_id="f_a",
            source_symbol_id="s_m",
            source_rel_path="m.py",
            source_line=1,
            source_col=0,
            rel_type=RelationshipType.IMPORTS,
            target_symbol_id=None,
            target_ref="external",
            resolution_status=ResolutionStatus.EXTERNAL,
            evidence="import external at m.py:1",
        ),
        RelationshipRecord(
            id="r_in",
            repository_id="repo_1",
            source_file_id="f_b",
            source_symbol_id="s_t",
            source_rel_path="other.py",
            source_line=1,
            source_col=0,
            rel_type=RelationshipType.IMPORTS,
            target_symbol_id="s_f",
            target_ref="m.f",
            resolution_status=ResolutionStatus.RESOLVED_STATIC,
            evidence="import m.f at other.py:1",
        ),
    ]
    explanation = explain_file(
        file,
        [module, function],
        relationships,
        [_metrics()],
        [EntryPoint(rel_path="m.py", line=5, evidence='if __name__ == "__main__"')],
        all_symbols=[module, function, target],
    )
    assert explanation.level is ExplanationLevel.FILE
    assert "Module docstring: Module docs." in explanation.facts
    assert any("Symbols: 1 function, 1 module." in fact for fact in explanation.facts)
    assert any("Top-level definitions: `m.f` (function)" in fact for fact in explanation.facts)
    assert any(
        "Imports: 1 statement reference(s) — 1 external." in fact for fact in explanation.facts
    )
    assert any("Entry point: line 5" in fact for fact in explanation.facts)
    assert any("Resolved inbound dependents: `other.py`" in fact for fact in explanation.facts)
    assert any("outside the analyzed repository" in fact for fact in explanation.facts)
    assert explanation.unknowns == []
    assert {section.title for section in explanation.sections} == {"Definitions", "Dependencies"}
    assert any(
        item.kind == "relationship" and item.relationship_id == "r_in"
        for item in explanation.evidence
    )


def test_file_explanation_records_parse_error_and_entry_point_error():
    file = FileDetail(
        id="f_bad",
        repository_id="repo_1",
        rel_path="bad.py",
        module_name="bad",
        size_bytes=10,
        content_hash="hash",
        parse_status=ParseStatus.PARSE_ERROR,
        error_message="invalid syntax",
        error_line=3,
        error_offset=1,
        source_available=True,
        content_changed_since_analysis=False,
    )
    explanation = explain_file(
        file, [], [], [], [], entry_point_error="entry-point detection failed: boom"
    )
    assert explanation.facts[0].startswith("File `bad.py`")
    assert any(
        "did not parse successfully" in unknown and "invalid syntax" in unknown
        for unknown in explanation.unknowns
    )
    assert "entry-point detection failed: boom" in explanation.unknowns
    assert explanation.sections == []


def test_folder_explanation_reports_internal_edges_cycles_and_hotspots():
    files = [_file("pkg/a.py", "f_a", "pkg.a"), _file("pkg/b.py", "f_b", "pkg.b")]
    m_a = _symbol(
        "s_a",
        "a",
        "pkg.a",
        kind=SymbolKind.MODULE,
        file_id="f_a",
        rel_path="pkg/a.py",
        start_line=1,
        end_line=10,
    )
    f_a = _symbol(
        "s_f", "f", "pkg.a.f", file_id="f_a", rel_path="pkg/a.py", start_line=2, end_line=4
    )
    m_b = _symbol(
        "s_b",
        "b",
        "pkg.b",
        kind=SymbolKind.MODULE,
        file_id="f_b",
        rel_path="pkg/b.py",
        start_line=1,
        end_line=10,
    )
    relationships = [
        RelationshipRecord(
            id="r_ab",
            repository_id="repo_1",
            source_file_id="f_a",
            source_symbol_id="s_a",
            source_rel_path="pkg/a.py",
            source_line=1,
            source_col=0,
            rel_type=RelationshipType.IMPORTS,
            target_symbol_id="s_b",
            target_ref="pkg.b",
            resolution_status=ResolutionStatus.RESOLVED_STATIC,
            evidence="import pkg.b at pkg/a.py:1",
        )
    ]
    metrics = [
        FunctionMetricsRecord(
            symbol_id="s_f",
            file_id="f_a",
            rel_path="pkg/a.py",
            qualified_name="pkg.a.f",
            start_line=2,
            end_line=4,
            line_count=3,
            cyclomatic_complexity=11,
        )
    ]
    architecture = ArchitectureSummary(
        repository_id="repo_1",
        files=2,
        dependency_edges=1,
        cycles=[
            DependencyCycle(
                files=["pkg/a.py", "pkg/b.py", "pkg/a.py"],
                edges=[
                    DependencyEdge(
                        source_rel_path="pkg/a.py",
                        target_rel_path="pkg/b.py",
                        line=1,
                        evidence="import pkg.b at pkg/a.py:1",
                    ),
                    DependencyEdge(
                        source_rel_path="pkg/b.py",
                        target_rel_path="pkg/a.py",
                        line=1,
                        evidence="import pkg.a at pkg/b.py:1",
                    ),
                ],
            )
        ],
    )
    explanation = explain_folder(
        "pkg", files, [m_a, f_a, m_b], relationships, metrics, architecture
    )
    assert explanation.level is ExplanationLevel.FOLDER
    assert "Folder `pkg` contains 2 analyzed file(s)." in explanation.facts
    assert "Internal resolved dependency edges: 1." in explanation.facts
    assert "Outgoing cross-boundary dependencies: 0." in explanation.facts
    assert "Import cycles entirely inside the folder: 1." in explanation.facts
    assert any("Cycle: `pkg/a.py` → `pkg/b.py` → `pkg/a.py`." in fact for fact in explanation.facts)
    assert any(
        "Highest-complexity functions: `pkg.a.f` (11)." in fact for fact in explanation.facts
    )
    assert any("path-based" in heuristic for heuristic in explanation.heuristics)
    assert any(
        item.kind == "relationship" and item.rel_path == "pkg/a.py" for item in explanation.evidence
    )


def test_folder_explanation_resolves_outgoing_targets_outside_the_folder():
    files = [_file("pkg/a.py", "f_a", "pkg.a")]
    m_a = _symbol(
        "s_a",
        "a",
        "pkg.a",
        kind=SymbolKind.MODULE,
        file_id="f_a",
        rel_path="pkg/a.py",
        start_line=1,
        end_line=10,
    )
    m_ext = _symbol(
        "s_ext_mod",
        "ext",
        "ext",
        kind=SymbolKind.MODULE,
        file_id="f_ext",
        rel_path="ext.py",
        start_line=1,
        end_line=10,
    )
    f_ext = _symbol(
        "s_ext",
        "helper",
        "ext.helper",
        file_id="f_ext",
        rel_path="ext.py",
        start_line=2,
        end_line=3,
    )
    relationships = [
        RelationshipRecord(
            id="r_out",
            repository_id="repo_1",
            source_file_id="f_a",
            source_symbol_id="s_a",
            source_rel_path="pkg/a.py",
            source_line=1,
            source_col=0,
            rel_type=RelationshipType.IMPORTS,
            target_symbol_id="s_ext",
            target_ref="ext.helper",
            resolution_status=ResolutionStatus.RESOLVED_STATIC,
            evidence="from ext import helper at pkg/a.py:1",
        )
    ]
    explanation = explain_folder(
        "pkg",
        files,
        [m_a],
        relationships,
        [],
        None,
        all_symbols=[m_a, m_ext, f_ext],
    )
    assert "Outgoing cross-boundary dependencies: 1." in explanation.facts
    assert any("Outgoing targets: `ext.py`." in fact for fact in explanation.facts)


def test_project_explanation_uses_architecture_summary_and_thresholds():
    cycle = DependencyCycle(
        files=["a.py", "b.py", "a.py"],
        edges=[
            DependencyEdge(
                source_rel_path="a.py", target_rel_path="b.py", line=1, evidence="a imports b"
            ),
            DependencyEdge(
                source_rel_path="b.py", target_rel_path="a.py", line=1, evidence="b imports a"
            ),
        ],
    )
    finding = ArchitectureFinding(
        id="find_1",
        kind="dependency_cycle",
        severity=FindingSeverity.WARNING,
        title="Dependency cycle",
        description="Resolved static imports form a cycle.",
        subject="a.py → b.py → a.py",
        rel_path="a.py",
        evidence=[EvidenceRef(kind="file", rel_path="a.py", line=1, detail="cycle member")],
        heuristic=False,
    )
    architecture = ArchitectureSummary(
        repository_id="repo_1",
        files=2,
        dependency_edges=2,
        cycles=[cycle],
        central_components=[
            CentralComponent(rel_path="a.py", in_degree=1, out_degree=1, degree=2, centrality=2.0)
        ],
        complexity_hotspots=[
            ComplexityHotspot(
                symbol_id="s_big",
                qualified_name="a.big",
                rel_path="a.py",
                start_line=2,
                end_line=40,
                cyclomatic_complexity=12,
                line_count=39,
                max_nesting_depth=3,
            )
        ],
        findings=[finding],
        disconnected_components=1,
        entry_points=[EntryPoint(rel_path="a.py", line=1, evidence='if __name__ == "__main__"')],
        thresholds={"complexity_warning": 10, "centrality": "degree centrality"},
        notes=["architecture note"],
        truncated=True,
    )
    files = [_file("a.py", "f_a", "a"), _file("b.py", "f_b", "b")]
    symbols = [
        _symbol("s_a", "a", "a", kind=SymbolKind.MODULE, file_id="f_a", rel_path="a.py"),
        _symbol("s_b", "b", "b", kind=SymbolKind.MODULE, file_id="f_b", rel_path="b.py"),
    ]
    metrics = [
        FunctionMetricsRecord(
            symbol_id="s_big",
            file_id="f_a",
            rel_path="a.py",
            qualified_name="a.big",
            start_line=2,
            end_line=40,
            line_count=39,
            cyclomatic_complexity=12,
        )
    ]
    explanation = explain_project("repo_1", files, symbols, metrics, architecture)
    assert explanation.level is ExplanationLevel.PROJECT
    assert "Repository `repo_1` contains 2 analyzed file(s) and 2 symbol(s)." in explanation.facts
    assert any("Resolved file dependency edges: 2" in fact for fact in explanation.facts)
    assert any("Import cycles detected: 1." in fact for fact in explanation.facts)
    assert any(
        "Most central files by degree centrality: `a.py` (2.0)." in fact
        for fact in explanation.facts
    )
    assert any("Highest-complexity functions: `a.big` (12)." in fact for fact in explanation.facts)
    assert "Architecture findings: 1." in explanation.facts
    assert any("structural indicators" in heuristic for heuristic in explanation.heuristics)
    assert (
        "Architecture analysis reached a configured bound and is incomplete."
        in explanation.unknowns
    )
    assert "architecture note" in explanation.unknowns
    assert [section.title for section in explanation.sections] == [
        "Architecture findings",
        "Thresholds",
    ]
    assert any(item.kind == "metric" and item.symbol_id == "s_big" for item in explanation.evidence)
