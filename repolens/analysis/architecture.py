"""Architecture analysis and review indicators (Phase 2, P2-07).

Everything here is derived from persisted analysis facts:

- **File dependency graph**: one node per analyzed file; one edge per distinct
  ``resolved_static`` import between two files. Calls are not treated as module
  dependencies for cycle detection.
- **Dependency cycles**: bounded ``simple_cycles`` over the import graph.
- **Central components**: degree centrality ``(in_degree + out_degree) / (N-1)``
  on the import graph (0 when ``N <= 1``).
- **Coupling**: fan-in/fan-out are distinct predecessor/successor files;
  instability is ``fan_out / (fan_in + fan_out)`` (Martin's instability),
  reported only when the denominator is non-zero.
- **Complexity hotspots**: the P2-01 AST decision-point approximation.
- **Entry points**: explicit ``if __name__ == "__main__"`` guards and
  ``__main__.py`` files only.

Thresholds are explicit and included in the response. Findings are labelled
``heuristic=True`` unless they are objective graph facts (cycles, disconnected
components). No finding claims that code is defective; each says what to
inspect and why.
"""

from __future__ import annotations

import itertools

import networkx as nx

from repolens.core.identifiers import stable_id
from repolens.core.models import (
    ArchitectureFinding,
    ArchitectureSummary,
    CentralComponent,
    ComplexityHotspot,
    CouplingMetric,
    DependencyCycle,
    DependencyEdge,
    EntryPoint,
    EvidenceRef,
    FileRecord,
    FindingSeverity,
    FunctionMetricsRecord,
    RelationshipRecord,
    RelationshipType,
    ResolutionStatus,
    SymbolRecord,
)

DEFAULT_COMPLEXITY_WARNING = 10
DEFAULT_FAN_OUT_WARNING = 10
DEFAULT_FAN_IN_WARNING = 10
DEFAULT_MAX_CYCLES = 50
DEFAULT_MAX_CYCLE_LENGTH = 10
DEFAULT_TOP_COMPONENTS = 10
DEFAULT_MAX_FINDINGS = 100
_COUPLING_DISPLAY_LIMIT = 50


def _finding_id(repo_id: str, kind: str, subject: str, detail: str) -> str:
    return stable_id("finding", repo_id, kind, subject, detail)


def _import_edges(
    files: list[FileRecord],
    symbols: list[SymbolRecord],
    relationships: list[RelationshipRecord],
) -> tuple[nx.DiGraph, dict[tuple[str, str], DependencyEdge]]:
    file_by_id = {f.id: f for f in files}
    symbol_by_id = {s.id: s for s in symbols}
    graph = nx.DiGraph()
    for file in sorted(files, key=lambda f: f.rel_path):
        graph.add_node(file.rel_path)
    edges: dict[tuple[str, str], DependencyEdge] = {}
    for rel in relationships:
        if rel.rel_type is not RelationshipType.IMPORTS:
            continue
        if rel.resolution_status is not ResolutionStatus.RESOLVED_STATIC:
            continue
        if not rel.target_symbol_id:
            continue
        source_file = file_by_id.get(rel.source_file_id)
        target_symbol = symbol_by_id.get(rel.target_symbol_id)
        if source_file is None or target_symbol is None:
            continue
        target_file = file_by_id.get(target_symbol.file_id)
        if target_file is None or target_file.rel_path == source_file.rel_path:
            continue
        key = (source_file.rel_path, target_file.rel_path)
        existing = edges.get(key)
        candidate = DependencyEdge(
            source_rel_path=source_file.rel_path,
            target_rel_path=target_file.rel_path,
            line=rel.source_line,
            evidence=rel.evidence,
        )
        if existing is None or (candidate.line, candidate.evidence) < (
            existing.line,
            existing.evidence,
        ):
            edges[key] = candidate
    for (source, target), edge in sorted(edges.items()):
        graph.add_edge(source, target, line=edge.line, evidence=edge.evidence)
    return graph, edges


def _cycles(
    graph: nx.DiGraph,
    edges: dict[tuple[str, str], DependencyEdge],
    *,
    max_cycles: int,
    max_cycle_length: int,
) -> tuple[list[DependencyCycle], bool]:
    if graph.number_of_nodes() == 0:
        return [], False
    try:
        generator = nx.simple_cycles(graph, length_bound=max_cycle_length)
    except TypeError:  # pragma: no cover - older networkx fallback
        generator = nx.simple_cycles(graph)
    found: list[list[str]] = []
    truncated = False
    for cycle in itertools.islice(generator, max_cycles + 1):
        if len(cycle) > max_cycle_length:
            continue
        if len(found) >= max_cycles:
            truncated = True
            break
        nodes = list(cycle)
        if nodes:
            # Canonical rotation makes cycle evidence deterministic regardless of
            # which node networkx happened to start from.
            start = min(range(len(nodes)), key=lambda index: nodes[index])
            nodes = nodes[start:] + nodes[:start]
        found.append(nodes)
    found.sort(key=lambda nodes: (len(nodes), tuple(nodes)))
    out: list[DependencyCycle] = []
    for nodes in found:
        loop = [*nodes, nodes[0]]
        cycle_edges: list[DependencyEdge] = []
        for source, target in zip(loop, loop[1:], strict=False):
            edge = edges.get((source, target))
            if edge is None:
                edge = DependencyEdge(
                    source_rel_path=source,
                    target_rel_path=target,
                    line=0,
                    evidence="import edge",
                )
            cycle_edges.append(edge)
        out.append(DependencyCycle(files=loop, edges=cycle_edges))
    return out, truncated


def _central_components(graph: nx.DiGraph, top: int) -> list[CentralComponent]:
    n = graph.number_of_nodes()
    components: list[CentralComponent] = []
    for node in sorted(graph.nodes()):
        in_degree = int(graph.in_degree(node))
        out_degree = int(graph.out_degree(node))
        degree = in_degree + out_degree
        centrality = degree / (n - 1) if n > 1 else 0.0
        components.append(
            CentralComponent(
                rel_path=str(node),
                in_degree=in_degree,
                out_degree=out_degree,
                degree=degree,
                centrality=round(centrality, 6),
            )
        )
    components.sort(key=lambda c: (-c.centrality, -c.degree, c.rel_path))
    return components[:top]


def _coupling(graph: nx.DiGraph) -> list[CouplingMetric]:
    metrics: list[CouplingMetric] = []
    for node in sorted(graph.nodes()):
        fan_in = len(set(graph.predecessors(node)))
        fan_out = len(set(graph.successors(node)))
        total = fan_in + fan_out
        instability = round(fan_out / total, 6) if total else None
        metrics.append(
            CouplingMetric(
                rel_path=str(node),
                fan_in=fan_in,
                fan_out=fan_out,
                instability=instability,
            )
        )
    metrics.sort(key=lambda m: (-(m.fan_in + m.fan_out), m.rel_path))
    return metrics


def _hotspots(metrics: list[FunctionMetricsRecord], top: int) -> list[ComplexityHotspot]:
    ordered = sorted(
        metrics,
        key=lambda m: (
            -m.cyclomatic_complexity,
            -m.line_count,
            m.rel_path,
            m.start_line,
            m.qualified_name,
        ),
    )
    return [
        ComplexityHotspot(
            symbol_id=m.symbol_id or None,
            qualified_name=m.qualified_name,
            rel_path=m.rel_path,
            start_line=m.start_line,
            end_line=m.end_line,
            cyclomatic_complexity=m.cyclomatic_complexity,
            line_count=m.line_count,
            max_nesting_depth=m.max_nesting_depth,
        )
        for m in ordered[:top]
    ]


def _file_evidence(
    rel_path: str, line: int | None = None, detail: str | None = None
) -> EvidenceRef:
    return EvidenceRef(kind="file", rel_path=rel_path, line=line, detail=detail)


def analyze_architecture(
    repository_id: str,
    files: list[FileRecord],
    symbols: list[SymbolRecord],
    relationships: list[RelationshipRecord],
    metrics: list[FunctionMetricsRecord],
    entry_points: list[EntryPoint],
    *,
    complexity_warning: int = DEFAULT_COMPLEXITY_WARNING,
    fan_out_warning: int = DEFAULT_FAN_OUT_WARNING,
    fan_in_warning: int = DEFAULT_FAN_IN_WARNING,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    max_cycle_length: int = DEFAULT_MAX_CYCLE_LENGTH,
    top_components: int = DEFAULT_TOP_COMPONENTS,
    max_findings: int = DEFAULT_MAX_FINDINGS,
) -> ArchitectureSummary:
    """Compute the architecture summary from persisted facts."""
    graph, edges = _import_edges(files, symbols, relationships)
    cycles, cycles_truncated = _cycles(
        graph, edges, max_cycles=max_cycles, max_cycle_length=max_cycle_length
    )
    central = _central_components(graph, top_components)
    coupling = _coupling(graph)
    hotspots = _hotspots(metrics, top_components)
    disconnected = nx.number_weakly_connected_components(graph) if graph.number_of_nodes() else 0

    findings: list[ArchitectureFinding] = []
    for cycle in cycles:
        subject = " → ".join(cycle.files)
        findings.append(
            ArchitectureFinding(
                id=_finding_id(repository_id, "dependency_cycle", subject, str(len(cycle.files))),
                kind="dependency_cycle",
                severity=FindingSeverity.WARNING,
                title=f"Dependency cycle across {len(cycle.files) - 1} file(s)",
                description=(
                    "Resolved static imports form a cycle. This is an objective graph fact;"
                    " it means the modules depend on each other, not that the code is wrong."
                ),
                subject=subject,
                rel_path=cycle.files[0] if cycle.files else None,
                metrics={"length": len(cycle.files) - 1},
                evidence=[_file_evidence(rel, detail="cycle member") for rel in cycle.files[:-1]]
                + [
                    EvidenceRef(
                        kind="relationship",
                        rel_path=edge.source_rel_path,
                        line=edge.line,
                        detail=f"{edge.source_rel_path} imports {edge.target_rel_path}:"
                        f" {edge.evidence}",
                    )
                    for edge in cycle.edges
                ],
                heuristic=False,
            )
        )

    by_path = {metric.rel_path: metric for metric in coupling}
    for component in central[:1]:
        if component.degree == 0:
            continue
        metric = by_path.get(component.rel_path)
        findings.append(
            ArchitectureFinding(
                id=_finding_id(
                    repository_id, "central_component", component.rel_path, str(component.degree)
                ),
                kind="central_component",
                severity=FindingSeverity.INFO,
                title=f"Central component: {component.rel_path}",
                description=(
                    "Highest degree centrality on the resolved-import graph."
                    " Centrality is a structural indicator, not a quality score."
                ),
                subject=component.rel_path,
                rel_path=component.rel_path,
                metrics={
                    "in_degree": component.in_degree,
                    "out_degree": component.out_degree,
                    "degree": component.degree,
                    "centrality": component.centrality,
                    "fan_in": metric.fan_in if metric else 0,
                    "fan_out": metric.fan_out if metric else 0,
                },
                evidence=[_file_evidence(component.rel_path, detail="central component")],
                heuristic=True,
            )
        )

    for metric in coupling:
        if metric.fan_out >= fan_out_warning:
            findings.append(
                ArchitectureFinding(
                    id=_finding_id(
                        repository_id, "high_fan_out", metric.rel_path, str(metric.fan_out)
                    ),
                    kind="high_fan_out",
                    severity=FindingSeverity.WARNING,
                    title=f"High fan-out: {metric.rel_path}",
                    description=(
                        f"This file imports {metric.fan_out} distinct analyzed files"
                        f" (threshold {fan_out_warning}). Review whether the dependency"
                        " surface is intentional."
                    ),
                    subject=metric.rel_path,
                    rel_path=metric.rel_path,
                    metrics={"fan_out": metric.fan_out, "threshold": fan_out_warning},
                    evidence=[_file_evidence(metric.rel_path, detail="fan-out")],
                    heuristic=True,
                )
            )
        if metric.fan_in >= fan_in_warning:
            findings.append(
                ArchitectureFinding(
                    id=_finding_id(
                        repository_id, "high_fan_in", metric.rel_path, str(metric.fan_in)
                    ),
                    kind="high_fan_in",
                    severity=FindingSeverity.WARNING,
                    title=f"High fan-in: {metric.rel_path}",
                    description=(
                        f"{metric.fan_in} distinct analyzed files import this file"
                        f" (threshold {fan_in_warning}). Changes here have a wider"
                        " structural blast radius."
                    ),
                    subject=metric.rel_path,
                    rel_path=metric.rel_path,
                    metrics={"fan_in": metric.fan_in, "threshold": fan_in_warning},
                    evidence=[_file_evidence(metric.rel_path, detail="fan-in")],
                    heuristic=True,
                )
            )

    for hotspot in hotspots:
        if hotspot.cyclomatic_complexity < complexity_warning:
            continue
        findings.append(
            ArchitectureFinding(
                id=_finding_id(
                    repository_id,
                    "complexity_hotspot",
                    hotspot.qualified_name,
                    str(hotspot.cyclomatic_complexity),
                ),
                kind="complexity_hotspot",
                severity=FindingSeverity.WARNING,
                title=f"Complexity hotspot: {hotspot.qualified_name}",
                description=(
                    f"AST decision-point approximation is {hotspot.cyclomatic_complexity}"
                    f" (threshold {complexity_warning}); {hotspot.line_count} lines,"
                    f" max nesting {hotspot.max_nesting_depth}."
                    " This is a reading-priority indicator, not proof of a defect."
                ),
                subject=hotspot.qualified_name,
                rel_path=hotspot.rel_path,
                symbol_id=hotspot.symbol_id,
                metrics={
                    "cyclomatic_complexity": hotspot.cyclomatic_complexity,
                    "line_count": hotspot.line_count,
                    "max_nesting_depth": hotspot.max_nesting_depth,
                    "threshold": complexity_warning,
                },
                evidence=[
                    EvidenceRef(
                        kind="metric",
                        rel_path=hotspot.rel_path,
                        line=hotspot.start_line,
                        end_line=hotspot.end_line,
                        symbol_id=hotspot.symbol_id,
                        detail="function metrics",
                    )
                ],
                heuristic=True,
            )
        )

    if disconnected > 1:
        findings.append(
            ArchitectureFinding(
                id=_finding_id(
                    repository_id, "disconnected_components", repository_id, str(disconnected)
                ),
                kind="disconnected_components",
                severity=FindingSeverity.INFO,
                title=f"{disconnected} weakly connected components",
                description=(
                    "The resolved-import graph is not connected. Isolated files may be"
                    " entry points, scripts, or files with only external dependencies."
                ),
                subject=repository_id,
                metrics={"components": disconnected},
                evidence=[_file_evidence(f.rel_path, detail="file node") for f in files[:20]],
                heuristic=False,
            )
        )

    severity_order = {FindingSeverity.WARNING: 0, FindingSeverity.INFO: 1}
    findings.sort(key=lambda f: (severity_order[f.severity], f.kind, f.subject, f.id))
    findings_truncated = len(findings) > max_findings
    findings = findings[:max_findings]

    thresholds: dict[str, float | int | str] = {
        "complexity_warning": complexity_warning,
        "fan_out_warning": fan_out_warning,
        "fan_in_warning": fan_in_warning,
        "max_cycles": max_cycles,
        "max_cycle_length": max_cycle_length,
        "top_components": top_components,
        "max_findings": max_findings,
        "centrality": "degree centrality (in+out)/(N-1) on the resolved-import file graph",
        "instability": "fan_out/(fan_in+fan_out) when fan_in+fan_out > 0",
        "complexity": "AST decision-point approximation from P2-01, not a CFG cyclomatic number",
    }
    notes = [
        "Dependency edges are resolved_static imports only; calls are not treated as module dependencies.",
        "Centrality and coupling are structural indicators, not quality scores.",
        "Complexity hotspots use the P2-01 AST approximation.",
        "Entry points are explicit __main__ guards or __main__.py files only.",
        "Exception-flow and runtime behavior are not modelled.",
    ]
    if cycles_truncated:
        notes.append(f"cycle enumeration stopped at max_cycles={max_cycles}")
    if findings_truncated:
        notes.append(f"findings truncated at max_findings={max_findings}")

    return ArchitectureSummary(
        repository_id=repository_id,
        files=len(files),
        dependency_edges=len(edges),
        cycles=cycles,
        central_components=central,
        coupling=coupling[:_COUPLING_DISPLAY_LIMIT],
        complexity_hotspots=hotspots,
        findings=findings,
        disconnected_components=disconnected,
        entry_points=sorted(entry_points, key=lambda e: (e.rel_path, e.line)),
        thresholds=thresholds,
        notes=notes,
        truncated=cycles_truncated or findings_truncated,
    )
