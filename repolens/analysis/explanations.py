"""Deterministic, evidence-backed explanations (Phase 2, P2-04).

Explanations are assembled from facts that already exist in the analysis:
symbols, metrics, CFG nodes/edges, data-flow results, and relationships. No
generative model is involved. Every sentence is either:

- a **fact** derived directly from a stored record or a deterministic analysis,
- a **heuristic** labelled as such (complexity/nesting/coupling indicators), or
- an **unknown**, stated explicitly when the available facts are insufficient.

Every section carries evidence references with repository-relative paths and
one-based line numbers. Statements never claim runtime behaviour or defect
status.
"""

from __future__ import annotations

from collections import Counter

from repolens.core.models import (
    ArchitectureSummary,
    CFGEdge,
    CFGNode,
    CFGNodeKind,
    DataFlowResult,
    DefinitionFact,
    EntryPoint,
    EvidenceRef,
    Explanation,
    ExplanationLevel,
    ExplanationSection,
    FileDetail,
    FileRecord,
    FunctionCFG,
    FunctionMetricsRecord,
    RelationshipRecord,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
    SymbolRecord,
)

_MAX_LIST = 25


def _short(text: str | None, limit: int = 200) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _symbol_evidence(symbol: SymbolRecord, detail: str | None = None) -> EvidenceRef:
    return EvidenceRef(
        kind="symbol",
        rel_path=symbol.rel_path,
        line=symbol.start_line,
        end_line=symbol.end_line,
        symbol_id=symbol.id,
        detail=detail or symbol.qualified_name,
    )


def _file_evidence(
    rel_path: str, line: int | None = None, detail: str | None = None
) -> EvidenceRef:
    return EvidenceRef(kind="file", rel_path=rel_path, line=line, detail=detail)


def _metric_evidence(metrics: FunctionMetricsRecord) -> EvidenceRef:
    return EvidenceRef(
        kind="metric",
        rel_path=metrics.rel_path,
        line=metrics.start_line,
        end_line=metrics.end_line,
        symbol_id=metrics.symbol_id or None,
        detail="function metrics",
    )


def _cfg_evidence(cfg: FunctionCFG, node: CFGNode | None = None) -> EvidenceRef:
    return EvidenceRef(
        kind="cfg",
        rel_path=cfg.rel_path,
        line=node.line if node else cfg.start_line,
        end_line=node.end_line if node else cfg.end_line,
        symbol_id=cfg.symbol_id or None,
        detail=f"CFG node {node.id}" if node else "function CFG",
    )


def _relationship_evidence(rel: RelationshipRecord) -> EvidenceRef:
    return EvidenceRef(
        kind="relationship",
        rel_path=rel.source_rel_path,
        line=rel.source_line,
        relationship_id=rel.id,
        symbol_id=rel.source_symbol_id,
        detail=f"{rel.rel_type.value} {rel.target_ref} [{rel.resolution_status.value}]",
    )


def _edge_line(edge: CFGEdge) -> str:
    return f"`{edge.source}` → `{edge.target}` ({edge.type.value})"


def _node_line(node: CFGNode) -> str:
    where = f"line {node.line}" if node.line else "line ?"
    return f"`{node.id}` {node.kind.value} at {where}: {_short(node.label, 120)}"


def _matching_node(cfg: FunctionCFG, line: int) -> CFGNode | None:
    covering = [
        node
        for node in cfg.nodes
        if node.line is not None and node.line <= line and (node.end_line or node.line) >= line
    ]
    # ``entry``, ``exit`` and ``merge`` are synthetic bookkeeping nodes. They can
    # share a source line with a real statement, so never prefer them when a
    # source-backed node covers the requested line.
    real = [node for node in covering if node.kind.value not in {"entry", "exit", "merge"}]
    if real:
        covering = real
    elif covering:
        return None
    if not covering:
        return None
    # Prefer the most specific (smallest span), then the latest start line.
    covering.sort(
        key=lambda node: (
            (node.end_line or node.line or 0) - (node.line or 0),
            -(node.line or 0),
            node.id,
        )
    )
    return covering[0]


def explain_statement(
    symbol: SymbolRecord,
    cfg: FunctionCFG,
    dataflow: DataFlowResult,
    line: int,
) -> Explanation:
    """Explain the statement node(s) covering one source line of a function."""
    node = _matching_node(cfg, line)
    facts: list[str] = []
    heuristics: list[str] = []
    unknowns: list[str] = []
    evidence: list[EvidenceRef] = [_symbol_evidence(symbol)]

    if node is None:
        unknowns.append(
            f"No CFG statement node covers line {line}. The line may be blank, a"
            " comment, a decorator, a continuation of a multi-line expression, or"
            " unreachable code omitted by the bounded CFG."
        )
        return Explanation(
            level=ExplanationLevel.STATEMENT,
            subject=f"{symbol.qualified_name}:{line}",
            title=f"Statement at {symbol.rel_path}:{line}",
            facts=facts,
            heuristics=heuristics,
            unknowns=unknowns,
            evidence=evidence,
        )

    facts.append(
        f"CFG node `{node.id}` is a `{node.kind.value}` statement at"
        f" {cfg.rel_path}:{node.line}"
        + (f"-{node.end_line}" if node.end_line and node.end_line != node.line else "")
        + "."
    )
    if node.label:
        facts.append(f"Source form: {_short(node.label, 240)}.")
    outgoing = [edge for edge in cfg.edges if edge.source == node.id]
    incoming = [edge for edge in cfg.edges if edge.target == node.id]
    if incoming:
        facts.append("Incoming edges: " + "; ".join(_edge_line(edge) for edge in incoming) + ".")
    else:
        facts.append("This node has no incoming edge; it is the CFG entry or unreachable.")
    if outgoing:
        facts.append("Outgoing edges: " + "; ".join(_edge_line(edge) for edge in outgoing) + ".")
    else:
        facts.append("This node has no outgoing edge; the path terminates here.")

    node_defs: list[DefinitionFact] = [
        definition for definition in dataflow.definitions if definition.node_id == node.id
    ]
    node_uses = [use for use in dataflow.uses if use.node_id == node.id]
    if node_defs:
        facts.append(
            "Local definitions here: "
            + ", ".join(f"`{d.name}` ({d.kind.value}, {d.id})" for d in node_defs)
            + "."
        )
    if node_uses:
        reach_by_use = {item.use_id: item.definitions for item in dataflow.reaching}
        parts: list[str] = []
        for use in node_uses:
            reaching = reach_by_use.get(use.id, [])
            if reaching:
                parts.append(f"`{use.name}` → {', '.join(reaching)}")
            else:
                parts.append(f"`{use.name}` → no locally reaching definition")
        facts.append("Local reads here: " + "; ".join(parts) + ".")

    if node.kind.value in {"condition", "loop"}:
        heuristics.append(
            "This node is a control-flow decision. It contributes to the AST"
            " decision-point complexity approximation and to branch coverage in"
            " the CFG; it is not a runtime profile."
        )
    if node.kind.value == "return":
        facts.append(
            "A return statement terminates this control-flow path and reaches the function exit."
        )
    if node.kind.value == "raise":
        facts.append(
            "A raise statement terminates this control-flow path; the CFG connects it"
            " to the function exit and, conservatively, to enclosing handlers."
        )
    if cfg.source_changed_since_analysis:
        unknowns.append(
            "The file content changed after the stored analysis; this CFG was built"
            " from the current file and may not match the persisted run."
        )
    if cfg.truncated:
        unknowns.append("The CFG was truncated at the configured node/edge/depth cap.")
    for note in cfg.notes:
        if note not in unknowns:
            unknowns.append(note)
    if dataflow.truncated:
        unknowns.append(
            "Local data flow was truncated at the configured definition/use/iteration cap;"
            " reads and definitions may be incomplete."
        )

    evidence.append(_cfg_evidence(cfg, node))
    section = ExplanationSection(
        title="Source",
        lines=[node.source] if node.source else ["Source text unavailable."],
        evidence=[_cfg_evidence(cfg, node)],
    )
    return Explanation(
        level=ExplanationLevel.STATEMENT,
        subject=f"{symbol.qualified_name}:{line}",
        title=f"Statement at {symbol.rel_path}:{line}",
        facts=facts,
        heuristics=heuristics,
        unknowns=unknowns,
        evidence=evidence,
        sections=[section],
    )


def explain_function(
    symbol: SymbolRecord,
    metrics: FunctionMetricsRecord | None,
    cfg: FunctionCFG,
    dataflow: DataFlowResult,
    relationships: list[RelationshipRecord],
    *,
    complexity_threshold: int = 10,
) -> Explanation:
    """Explain one function/method from its stored and computed facts."""
    facts: list[str] = [
        f"{symbol.kind.value.capitalize()} `{symbol.qualified_name}` in"
        f" {symbol.rel_path}, lines {symbol.start_line}-{symbol.end_line}."
    ]
    heuristics: list[str] = []
    unknowns: list[str] = []
    evidence: list[EvidenceRef] = [_symbol_evidence(symbol)]

    if symbol.signature:
        facts.append(f"Signature: `{_short(symbol.signature, 300)}`.")
    if symbol.parameters:
        params = []
        for param in symbol.parameters:
            text = param.name
            if param.annotation:
                text += f": {param.annotation}"
            if param.default:
                text += f" = {param.default}"
            params.append(text)
        facts.append(f"Parameters: {', '.join(params)}.")
    else:
        facts.append("Parameters: none recorded.")
    if symbol.return_annotation:
        facts.append(f"Return annotation: `{symbol.return_annotation}`.")
    if symbol.docstring:
        facts.append(f"Docstring: {_short(symbol.docstring, 240)}")

    if metrics is not None:
        facts.append(
            "Metrics: "
            f"{metrics.line_count} lines, {metrics.statement_count} statement(s),"
            f" {metrics.return_count} return(s),"
            f" {metrics.call_count} call(s), {metrics.if_count} if,"
            f" {metrics.for_count} for, {metrics.while_count} while,"
            f" {metrics.try_count} try, {metrics.with_count} with,"
            f" {metrics.match_count} match, max nesting {metrics.max_nesting_depth}."
        )
        if metrics.cyclomatic_complexity >= complexity_threshold:
            heuristics.append(
                f"AST decision-point complexity is {metrics.cyclomatic_complexity}"
                f" (review threshold {complexity_threshold}). This is a"
                " reading-priority indicator, not proof of a defect and not a CFG"
                " cyclomatic number."
            )
        else:
            heuristics.append(
                f"AST decision-point complexity is {metrics.cyclomatic_complexity}"
                f" (below the review threshold {complexity_threshold})."
            )
        evidence.append(_metric_evidence(metrics))
    else:
        unknowns.append("No persisted metrics are available for this function symbol.")

    facts.append(
        f"Control-flow graph: {len(cfg.nodes)} node(s), {len(cfg.edges)} edge(s),"
        f" entry `{cfg.entry}`, exit `{cfg.exit}`."
    )
    if cfg.truncated:
        unknowns.append("The CFG reached a configured resource cap and is incomplete.")
    if cfg.source_changed_since_analysis:
        unknowns.append(
            "The file changed after the stored analysis; the CFG and data flow were"
            " rebuilt from current source and may not match the run's stored facts."
        )
    for note in cfg.notes:
        if note not in unknowns:
            unknowns.append(note)
    evidence.append(_cfg_evidence(cfg))

    flow_kinds = {
        CFGNodeKind.CONDITION,
        CFGNodeKind.LOOP,
        CFGNodeKind.MATCH,
        CFGNodeKind.CASE,
        CFGNodeKind.TRY,
        CFGNodeKind.HANDLER,
        CFGNodeKind.WITH,
    }
    flow_nodes = [node for node in cfg.nodes if node.kind in flow_kinds]
    if flow_nodes:
        facts.append(
            f"Control-flow constructs: {len(flow_nodes)} condition/loop/case/"
            "exception/with node(s)."
        )
        for node in flow_nodes[:10]:
            label = _short(node.label, 120) if node.label else node.kind.value
            location = f"line {node.line}" if node.line is not None else "line unknown"
            facts.append(f"`{label}` [{node.kind.value}] at {location}.")
        if len(flow_nodes) > 10:
            facts.append(
                f"Only the first 10 of {len(flow_nodes)} control-flow constructs are listed."
            )
    else:
        facts.append("Control-flow constructs: none recorded (straight-line body).")

    facts.append(
        f"Local data flow: {len(dataflow.definitions)} definition(s),"
        f" {len(dataflow.uses)} use(s), {len(dataflow.undefined_uses)} use(s)"
        " without a locally reaching definition."
    )
    if dataflow.non_local_names:
        facts.append("Declared non-local names: " + ", ".join(dataflow.non_local_names) + ".")
    if dataflow.undefined_uses:
        use_by_id = {use.id: use for use in dataflow.uses}
        examples = [
            f"`{use_by_id[uid].name}` at line {use_by_id[uid].line}"
            for uid in dataflow.undefined_uses[:10]
            if uid in use_by_id
        ]
        facts.append(
            "Uses with no locally reaching definition (may be globals, builtins,"
            " closures, or comprehension variables): " + ", ".join(examples) + "."
        )
    if dataflow.truncated:
        unknowns.append(
            "The data-flow result reached a configured CFG/data-flow cap and is incomplete."
        )
    for note in dataflow.notes:
        if note not in unknowns:
            unknowns.append(note)
    evidence.append(
        EvidenceRef(
            kind="cfg",
            rel_path=cfg.rel_path,
            line=cfg.start_line,
            end_line=cfg.end_line,
            symbol_id=symbol.id,
            detail="local data flow",
        )
    )

    outgoing_calls = [
        rel
        for rel in relationships
        if rel.source_symbol_id == symbol.id and rel.rel_type is RelationshipType.CALLS
    ]
    if outgoing_calls:
        resolved = [
            rel
            for rel in outgoing_calls
            if rel.resolution_status is ResolutionStatus.RESOLVED_STATIC
        ]
        facts.append(
            f"Outgoing calls: {len(outgoing_calls)} reference(s),"
            f" {len(resolved)} resolved statically."
        )
        for rel in outgoing_calls[:10]:
            facts.append(
                f"Call `{_short(rel.call_expr or rel.target_ref, 120)}` →"
                f" `{rel.target_ref}` [{rel.resolution_status.value}] at line {rel.source_line}."
            )
            evidence.append(_relationship_evidence(rel))
    else:
        facts.append("Outgoing calls: none recorded.")

    incoming_calls = [
        rel
        for rel in relationships
        if rel.target_symbol_id == symbol.id and rel.rel_type is RelationshipType.CALLS
    ]
    if incoming_calls:
        facts.append(
            f"Incoming calls: {len(incoming_calls)} resolved call reference(s) from other symbols."
        )
        for rel in incoming_calls[:10]:
            facts.append(
                f"Called by `{rel.source_symbol_id}` at {rel.source_rel_path}:{rel.source_line}."
            )
            evidence.append(_relationship_evidence(rel))
    else:
        facts.append("Incoming calls: none resolved within the repository.")

    sections = [
        ExplanationSection(
            title="Signature",
            lines=[
                f"`{symbol.qualified_name}`"
                + (f" — `{symbol.signature}`" if symbol.signature else ""),
                f"Defined at {symbol.rel_path}:{symbol.start_line}-{symbol.end_line}.",
            ],
            evidence=[_symbol_evidence(symbol)],
        )
    ]
    if metrics is not None:
        sections.append(
            ExplanationSection(
                title="Structural metrics",
                lines=[
                    f"{key}: {value}"
                    for key, value in {
                        "lines": metrics.line_count,
                        "statements": metrics.statement_count,
                        "parameters": metrics.parameter_count,
                        "returns": metrics.return_count,
                        "calls": metrics.call_count,
                        "if": metrics.if_count,
                        "for": metrics.for_count,
                        "while": metrics.while_count,
                        "try": metrics.try_count,
                        "with": metrics.with_count,
                        "match": metrics.match_count,
                        "max_nesting_depth": metrics.max_nesting_depth,
                        "cyclomatic_approximation": metrics.cyclomatic_complexity,
                    }.items()
                ],
                evidence=[_metric_evidence(metrics)],
            )
        )
    if flow_nodes:
        flow_lines = []
        for node in flow_nodes[:10]:
            label = _short(node.label, 140) if node.label else node.kind.value
            location = f"line {node.line}" if node.line is not None else "line unknown"
            flow_lines.append(f"{node.kind.value}: `{label}` at {location}")
        if len(flow_nodes) > 10:
            flow_lines.append(
                f"... {len(flow_nodes) - 10} more control-flow construct(s) not listed."
            )
        sections.append(
            ExplanationSection(
                title="Control flow",
                lines=flow_lines,
                evidence=[_cfg_evidence(cfg)],
            )
        )
    return Explanation(
        level=ExplanationLevel.FUNCTION,
        subject=symbol.qualified_name,
        title=f"Function {symbol.qualified_name}",
        facts=facts,
        heuristics=heuristics,
        unknowns=unknowns,
        evidence=evidence,
        sections=sections,
    )


def explain_file(
    file: FileDetail,
    symbols: list[SymbolRecord],
    relationships: list[RelationshipRecord],
    metrics: list[FunctionMetricsRecord],
    entry_points: list[EntryPoint],
    *,
    all_symbols: list[SymbolRecord] | None = None,
    entry_point_error: str | None = None,
) -> Explanation:
    """Explain one file from persisted facts (fresh parse only for entry points)."""
    facts: list[str] = [
        f"File `{file.rel_path}` (module `{file.module_name}`),"
        f" parse status `{file.parse_status.value}`, {file.size_bytes} bytes."
    ]
    unknowns: list[str] = []
    heuristics: list[str] = []
    evidence: list[EvidenceRef] = [_file_evidence(file.rel_path, detail="file record")]

    if file.parse_status.value != "parsed":
        unknowns.append(
            "The file did not parse successfully; no symbols, imports, calls, or"
            f" metrics exist for it. Error: {_short(file.error_message, 240)}"
            + (f" (line {file.error_line})." if file.error_line else ".")
        )
        if entry_point_error:
            unknowns.append(entry_point_error)
        return Explanation(
            level=ExplanationLevel.FILE,
            subject=file.rel_path,
            title=f"File {file.rel_path}",
            facts=facts,
            heuristics=heuristics,
            unknowns=unknowns,
            evidence=evidence,
        )

    module_symbol = next((s for s in symbols if s.kind is SymbolKind.MODULE), None)
    if module_symbol is not None and module_symbol.docstring:
        facts.append(f"Module docstring: {_short(module_symbol.docstring, 300)}")
    counts = Counter(symbol.kind.value for symbol in symbols)
    facts.append(
        "Symbols: " + ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items())) + "."
    )
    top_level = [
        s for s in symbols if module_symbol is not None and s.parent_id == module_symbol.id
    ]
    if top_level:
        facts.append(
            "Top-level definitions: "
            + ", ".join(f"`{s.qualified_name}` ({s.kind.value})" for s in top_level[:_MAX_LIST])
            + "."
        )

    imports = [
        rel
        for rel in relationships
        if rel.rel_type is RelationshipType.IMPORTS and rel.source_file_id == file.id
    ]
    calls = [
        rel
        for rel in relationships
        if rel.rel_type is RelationshipType.CALLS and rel.source_file_id == file.id
    ]
    statuses = Counter(rel.resolution_status.value for rel in imports)
    facts.append(
        f"Imports: {len(imports)} statement reference(s) — "
        + ", ".join(f"{count} {status}" for status, count in sorted(statuses.items()))
        + "."
    )
    for rel in imports[:10]:
        facts.append(
            f"Import `{rel.target_ref}` [{rel.resolution_status.value}] at line {rel.source_line}."
        )
        evidence.append(_relationship_evidence(rel))
    if calls:
        facts.append(f"Calls: {len(calls)} call reference(s) originate in this file.")
        for rel in calls[:10]:
            evidence.append(_relationship_evidence(rel))

    file_symbol_ids = {s.id for s in symbols}
    symbol_by_id = {s.id: s for s in (all_symbols or symbols)}
    outgoing_targets: set[str] = set()
    for rel in imports:
        if rel.resolution_status is ResolutionStatus.RESOLVED_STATIC and rel.target_symbol_id:
            target = symbol_by_id.get(rel.target_symbol_id)
            if target is not None and target.file_id != file.id:
                outgoing_targets.add(target.rel_path)
    inbound_sources: set[str] = set()
    inbound_rels: list[RelationshipRecord] = []
    for rel in relationships:
        if rel.rel_type is not RelationshipType.IMPORTS:
            continue
        if rel.resolution_status is not ResolutionStatus.RESOLVED_STATIC:
            continue
        if rel.target_symbol_id in file_symbol_ids and rel.source_file_id != file.id:
            inbound_sources.add(rel.source_rel_path)
            inbound_rels.append(rel)
    if outgoing_targets:
        facts.append(
            "Resolved outgoing dependencies: "
            + ", ".join(f"`{path}`" for path in sorted(outgoing_targets)[:_MAX_LIST])
            + "."
        )
    else:
        facts.append("Resolved outgoing dependencies: none within the repository.")
    if inbound_sources:
        facts.append(
            "Resolved inbound dependents: "
            + ", ".join(f"`{path}`" for path in sorted(inbound_sources)[:_MAX_LIST])
            + "."
        )
        for rel in inbound_rels[:10]:
            evidence.append(_relationship_evidence(rel))
    else:
        facts.append("Resolved inbound dependents: none within the repository.")

    if metrics:
        max_complexity = max(m.cyclomatic_complexity for m in metrics)
        total_lines = sum(m.line_count for m in metrics)
        facts.append(
            f"Functions: {len(metrics)}; total function lines: {total_lines};"
            f" maximum AST decision-point complexity: {max_complexity}."
        )
        heuristics.append(
            "File-level complexity is summarised from function metrics; it is a"
            " reading-priority indicator, not a defect verdict."
        )
        for metric in metrics:
            evidence.append(_metric_evidence(metric))

    if entry_points:
        for entry in entry_points:
            facts.append(f"Entry point: line {entry.line} ({entry.evidence}).")
            evidence.append(_file_evidence(entry.rel_path, entry.line, entry.evidence))
    elif not file.source_available or entry_point_error:
        facts.append("Entry point: detection could not run against the current source.")
    else:
        facts.append("Entry point: no explicit `__main__` guard or `__main__.py` name detected.")

    if not file.source_available:
        unknowns.append("Current source is unavailable; entry-point detection could not run.")
    if entry_point_error:
        unknowns.append(entry_point_error)
    if file.content_changed_since_analysis:
        unknowns.append(
            "The file changed after the stored analysis; facts here come from the"
            " persisted run, while entry-point detection used current source."
        )
    if (
        statuses.get("unresolved")
        or statuses.get("ambiguous")
        or statuses.get("dynamic_or_unknown")
    ):
        heuristics.append(
            "Some references are unresolved, ambiguous, or dynamic; dependency and"
            " call summaries for this file are therefore conservative."
        )
    if any(rel.resolution_status is ResolutionStatus.EXTERNAL for rel in imports):
        facts.append("At least one import points outside the analyzed repository (external).")

    sections = [
        ExplanationSection(
            title="Definitions",
            lines=[
                f"`{s.qualified_name}` — {s.kind.value}, lines {s.start_line}-{s.end_line}"
                for s in symbols[:_MAX_LIST]
            ]
            or ["No symbols."],
            evidence=[_symbol_evidence(s) for s in symbols[:_MAX_LIST]],
        ),
        ExplanationSection(
            title="Dependencies",
            lines=[
                f"`{rel.target_ref}` [{rel.resolution_status.value}] line {rel.source_line}"
                for rel in imports[:_MAX_LIST]
            ]
            or ["No imports."],
            evidence=[_relationship_evidence(rel) for rel in imports[:_MAX_LIST]],
        ),
    ]
    return Explanation(
        level=ExplanationLevel.FILE,
        subject=file.rel_path,
        title=f"File {file.rel_path}",
        facts=facts,
        heuristics=heuristics,
        unknowns=unknowns,
        evidence=evidence,
        sections=sections,
    )


def explain_folder(
    prefix: str,
    files: list[FileRecord],
    symbols: list[SymbolRecord],
    relationships: list[RelationshipRecord],
    metrics: list[FunctionMetricsRecord],
    architecture: ArchitectureSummary | None,
    *,
    all_symbols: list[SymbolRecord] | None = None,
) -> Explanation:
    """Explain a path-based folder/module grouping."""
    label = prefix or "."
    facts: list[str] = [f"Folder `{label}` contains {len(files)} analyzed file(s)."]
    unknowns: list[str] = []
    heuristics: list[str] = [
        "Folder grouping is path-based; it is not a Python package boundary and"
        " does not imply architectural ownership."
    ]
    evidence: list[EvidenceRef] = [
        _file_evidence(file.rel_path, detail="folder member") for file in files[:20]
    ]
    evidence.extend(_symbol_evidence(symbol) for symbol in symbols[:20])
    counts = Counter(symbol.kind.value for symbol in symbols)
    facts.append(
        "Symbols: "
        + (", ".join(f"{count} {kind}" for kind, count in sorted(counts.items())) or "none")
        + "."
    )
    file_paths = {file.rel_path for file in files}
    file_ids = {file.id for file in files}
    # Outgoing imports can target symbols outside the folder, so resolve against
    # the full repository symbol set when the caller can provide it.
    symbol_by_id = {symbol.id: symbol for symbol in (all_symbols or symbols)}
    internal: set[tuple[str, str]] = set()
    outgoing: set[tuple[str, str]] = set()
    incoming: set[tuple[str, str]] = set()
    internal_relationships: list[RelationshipRecord] = []
    cross_boundary_relationships: list[RelationshipRecord] = []
    for rel in relationships:
        if rel.rel_type is not RelationshipType.IMPORTS:
            continue
        if rel.resolution_status is not ResolutionStatus.RESOLVED_STATIC:
            continue
        if not rel.target_symbol_id:
            continue
        target = symbol_by_id.get(rel.target_symbol_id)
        if target is None:
            continue
        source_in = rel.source_file_id in file_ids
        target_in = target.file_id in file_ids
        if source_in and target_in and rel.source_rel_path != target.rel_path:
            internal.add((rel.source_rel_path, target.rel_path))
            internal_relationships.append(rel)
        elif source_in and not target_in:
            outgoing.add((rel.source_rel_path, target.rel_path))
            cross_boundary_relationships.append(rel)
        elif target_in and not source_in:
            incoming.add((rel.source_rel_path, target.rel_path))
            cross_boundary_relationships.append(rel)
    for rel in internal_relationships[:20]:
        evidence.append(_relationship_evidence(rel))
    for rel in cross_boundary_relationships[:20]:
        evidence.append(_relationship_evidence(rel))
    facts.append(f"Internal resolved dependency edges: {len(internal)}.")
    facts.append(f"Outgoing cross-boundary dependencies: {len(outgoing)}.")
    facts.append(f"Incoming cross-boundary dependencies: {len(incoming)}.")
    if outgoing:
        facts.append(
            "Outgoing targets: "
            + ", ".join(f"`{target}`" for _, target in sorted(outgoing)[:20])
            + "."
        )
    if incoming:
        facts.append(
            "Incoming sources: "
            + ", ".join(f"`{source}`" for source, _ in sorted(incoming)[:20])
            + "."
        )

    if architecture is not None:
        cycles = [
            cycle
            for cycle in architecture.cycles
            if all(path in file_paths for path in cycle.files)
        ]
        if cycles:
            facts.append(f"Import cycles entirely inside the folder: {len(cycles)}.")
            for cycle in cycles[:10]:
                facts.append("Cycle: " + " → ".join(f"`{path}`" for path in cycle.files) + ".")
                for edge in cycle.edges:
                    evidence.append(
                        EvidenceRef(
                            kind="relationship",
                            rel_path=edge.source_rel_path,
                            line=edge.line,
                            detail=f"imports {edge.target_rel_path}: {edge.evidence}",
                        )
                    )
        else:
            facts.append(
                "Import cycles entirely inside the folder: none detected"
                + (" in the bounded analysis." if architecture.truncated else ".")
            )
        if architecture.truncated:
            unknowns.append(
                "The architecture summary reached a configured bound; architecture-derived"
                " folder facts may be incomplete."
            )
    else:
        unknowns.append("Architecture summary was not available for cycle filtering.")

    folder_metrics = [metric for metric in metrics if metric.rel_path in file_paths]
    if folder_metrics:
        ordered = sorted(
            folder_metrics,
            key=lambda m: (-m.cyclomatic_complexity, m.rel_path, m.start_line),
        )
        facts.append(
            "Highest-complexity functions: "
            + ", ".join(f"`{m.qualified_name}` ({m.cyclomatic_complexity})" for m in ordered[:10])
            + "."
        )
        heuristics.append(
            "Complexity values are AST decision-point approximations and are"
            " useful for reading order, not for grading code quality."
        )

    facts.append(
        "Parse status: "
        + ", ".join(
            f"{status}: {count}"
            for status, count in sorted(Counter(f.parse_status.value for f in files).items())
        )
        + "."
    )
    return Explanation(
        level=ExplanationLevel.FOLDER,
        subject=label,
        title=f"Folder {label}",
        facts=facts,
        heuristics=heuristics,
        unknowns=unknowns,
        evidence=evidence,
    )


def explain_project(
    repository_id: str,
    files: list[FileRecord],
    symbols: list[SymbolRecord],
    metrics: list[FunctionMetricsRecord],
    architecture: ArchitectureSummary,
) -> Explanation:
    """Explain a repository from its architecture summary and persisted facts."""
    counts = Counter(symbol.kind.value for symbol in symbols)
    statuses = Counter(file.parse_status.value for file in files)
    facts: list[str] = [
        f"Repository `{repository_id}` contains {len(files)} analyzed file(s) and"
        f" {len(symbols)} symbol(s)."
    ]
    facts.append(
        "Symbols by kind: "
        + ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
        + "."
    )
    facts.append(
        "Parse status: "
        + ", ".join(f"{status}: {count}" for status, count in sorted(statuses.items()))
        + "."
    )
    facts.append(
        f"Resolved file dependency edges: {architecture.dependency_edges};"
        f" weakly connected components: {architecture.disconnected_components}."
    )
    if architecture.entry_points:
        facts.append(
            "Entry points: "
            + ", ".join(
                f"`{entry.rel_path}:{entry.line}` ({entry.evidence})"
                for entry in architecture.entry_points[:10]
            )
            + "."
        )
    else:
        facts.append(
            "Entry points: no explicit `__main__` guard or `__main__.py` detected"
            + (" in the bounded scan." if architecture.truncated else ".")
        )
    if architecture.cycles:
        facts.append(f"Import cycles detected: {len(architecture.cycles)}.")
        for cycle in architecture.cycles[:10]:
            facts.append("Cycle: " + " → ".join(f"`{path}`" for path in cycle.files) + ".")
    else:
        facts.append(
            "Import cycles detected: none"
            + (" in the bounded analysis." if architecture.truncated else ".")
        )
    if architecture.central_components:
        facts.append(
            "Most central files by degree centrality: "
            + ", ".join(
                f"`{component.rel_path}` ({component.centrality})"
                for component in architecture.central_components[:5]
            )
            + "."
        )
    if architecture.complexity_hotspots:
        facts.append(
            "Highest-complexity functions: "
            + ", ".join(
                f"`{hotspot.qualified_name}` ({hotspot.cyclomatic_complexity})"
                for hotspot in architecture.complexity_hotspots[:5]
            )
            + "."
        )
    facts.append(f"Architecture findings: {len(architecture.findings)}.")

    heuristics: list[str] = [
        "Centrality, coupling, and complexity are structural indicators, not"
        " quality scores; no finding claims that code is defective.",
        "Complexity uses the P2-01 AST decision-point approximation.",
    ]
    unknowns: list[str] = []
    if architecture.truncated:
        unknowns.append("Architecture analysis reached a configured bound and is incomplete.")
    for note in architecture.notes:
        unknowns.append(note)
    evidence: list[EvidenceRef] = [
        _file_evidence(file.rel_path, detail="repository file") for file in files[:20]
    ]
    evidence.extend(_symbol_evidence(symbol) for symbol in symbols[:20])
    for metric in metrics[:20]:
        evidence.append(_metric_evidence(metric))
    for entry in architecture.entry_points[:20]:
        evidence.append(_file_evidence(entry.rel_path, entry.line, entry.evidence))
    for cycle in architecture.cycles[:20]:
        for edge in cycle.edges[:20]:
            evidence.append(
                EvidenceRef(
                    kind="relationship",
                    rel_path=edge.source_rel_path,
                    line=edge.line,
                    detail=f"imports {edge.target_rel_path}: {edge.evidence}",
                )
            )
    for finding in architecture.findings[:20]:
        evidence.extend(finding.evidence[:3])

    sections = [
        ExplanationSection(
            title="Architecture findings",
            lines=[
                f"[{finding.severity.value}] {finding.title} — {finding.description}"
                for finding in architecture.findings[:10]
            ]
            or ["No findings."],
            evidence=[
                evidence_ref
                for finding in architecture.findings[:10]
                for evidence_ref in finding.evidence[:2]
            ],
        ),
        ExplanationSection(
            title="Thresholds",
            lines=[f"{key}: {value}" for key, value in sorted(architecture.thresholds.items())],
            evidence=[],
        ),
    ]
    return Explanation(
        level=ExplanationLevel.PROJECT,
        subject=repository_id,
        title=f"Project {repository_id}",
        facts=facts,
        heuristics=heuristics,
        unknowns=unknowns,
        evidence=evidence,
        sections=sections,
    )
