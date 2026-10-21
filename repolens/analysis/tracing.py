"""Bounded, evidence-backed cross-file tracing (Phase 2, P2-06).

Traversal uses persisted relationships, not only graph edges:

- **Resolved edges** (``resolved_static`` with a target symbol) are followed.
- **Unresolved / external / ambiguous / dynamic references** are reported at the
  node where they originate and are never traversed. This makes the boundary of
  static knowledge explicit.
- Traversal is bounded by depth and visited-node count; cycles are detected and
  duplicate paths are suppressed by a visited set.
- An ``OUT`` trace answers "what can this symbol reach?"; ``IN`` answers "what
  can reach this symbol?"; ``BOTH`` explores both directions. Edges always keep
  their stored direction, so an ``IN`` step is labelled with the relationship as
  it exists in the repository.
- A path is a **static relationship path**, not a runtime execution trace.
"""

from __future__ import annotations

from collections import deque

from repolens.core.models import (
    Direction,
    RelationshipRecord,
    RelationshipType,
    ResolutionStatus,
    SymbolKind,
    SymbolRecord,
    TraceConnection,
    TraceEdge,
    TraceNode,
    TraceResult,
)

DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_NODES = 500
HARD_MAX_DEPTH = 8
HARD_MAX_NODES = 5_000
MAX_CYCLES = 50


def _edge(rel: RelationshipRecord) -> TraceEdge:
    return TraceEdge(
        source_symbol_id=rel.source_symbol_id or "",
        target_symbol_id=rel.target_symbol_id,
        rel_type=rel.rel_type,
        resolution_status=rel.resolution_status,
        rel_path=rel.source_rel_path,
        line=rel.source_line,
        col=rel.source_col,
        evidence=rel.evidence,
        call_expr=rel.call_expr,
        target_ref=rel.target_ref,
    )


def _edge_key(edge: TraceEdge) -> tuple[str, str, str, int, int, str]:
    return (
        edge.source_symbol_id,
        edge.target_symbol_id or "",
        edge.rel_type.value,
        edge.line,
        edge.col,
        edge.resolution_status.value,
    )


def _sort_key(rel: RelationshipRecord) -> tuple[str, str, int, int, str]:
    return (
        rel.rel_type.value,
        rel.source_rel_path,
        rel.source_line,
        rel.source_col,
        rel.target_symbol_id or rel.target_ref,
    )


def _is_ancestor(ancestor: str, node: str, parent: dict[str, str]) -> bool:
    current = node
    seen: set[str] = set()
    while current in parent and current not in seen:
        seen.add(current)
        current = parent[current]
        if current == ancestor:
            return True
    return current == ancestor


def _cycle_path(ancestor: str, node: str, parent: dict[str, str]) -> list[str]:
    chain = [node]
    while chain[-1] != ancestor:
        chain.append(parent[chain[-1]])
    chain.reverse()
    chain.append(ancestor)
    return chain


def _canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    if len(cycle) <= 1:
        return tuple(cycle)
    body = cycle[:-1]
    if not body:
        return tuple(cycle)
    start = min(range(len(body)), key=lambda i: body[i])
    rotated = body[start:] + body[:start]
    return tuple(rotated)


def _index_relationships(
    relationships: list[RelationshipRecord],
) -> tuple[
    dict[str, list[RelationshipRecord]],
    dict[str, list[RelationshipRecord]],
]:
    out: dict[str, list[RelationshipRecord]] = {}
    inbound: dict[str, list[RelationshipRecord]] = {}
    for rel in relationships:
        if rel.source_symbol_id:
            out.setdefault(rel.source_symbol_id, []).append(rel)
        if rel.target_symbol_id:
            inbound.setdefault(rel.target_symbol_id, []).append(rel)
    for table in (out, inbound):
        for key in table:
            table[key].sort(key=_sort_key)
    return out, inbound


def trace_symbol(
    repository_id: str,
    root: SymbolRecord,
    symbols: list[SymbolRecord],
    relationships: list[RelationshipRecord],
    *,
    direction: Direction = Direction.OUT,
    rel_types: set[RelationshipType] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> TraceResult:
    """Bounded BFS over resolved relationships from ``root``."""
    depth_cap = max(1, min(max_depth, HARD_MAX_DEPTH))
    node_cap = max(1, min(max_nodes, HARD_MAX_NODES))
    symbol_by_id = {s.id: s for s in symbols}
    out_index, in_index = _index_relationships(relationships)
    root_node = TraceNode(
        symbol_id=root.id,
        qualified_name=root.qualified_name,
        kind=root.kind,
        rel_path=root.rel_path,
        start_line=root.start_line,
        depth=0,
    )
    nodes: list[TraceNode] = [root_node]
    nodes_by_id: dict[str, TraceNode] = {root.id: root_node}
    edges: list[TraceEdge] = []
    seen_edges: set[tuple[str, str, str, int, int, str]] = set()
    unresolved: list[TraceEdge] = []
    seen_unresolved: set[tuple[str, str, str, int, int, str]] = set()
    cycles: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()
    visited: dict[str, int] = {root.id: 0}
    parent: dict[str, str] = {}
    parent_edge: dict[str, RelationshipRecord] = {}
    queue: deque[str] = deque([root.id])
    depth_capped = False
    node_capped = False
    cycle_capped = False

    def matches(rel: RelationshipRecord) -> bool:
        return rel_types is None or rel.rel_type in rel_types

    def collect_unresolved(current: str) -> None:
        for rel in out_index.get(current, []):
            if not matches(rel):
                continue
            if (
                rel.resolution_status is ResolutionStatus.RESOLVED_STATIC
                and rel.target_symbol_id is not None
                and rel.target_symbol_id in symbol_by_id
            ):
                continue
            edge = _edge(rel)
            key = _edge_key(edge)
            if key in seen_unresolved:
                continue
            seen_unresolved.add(key)
            unresolved.append(edge)
            node = nodes_by_id.get(current)
            if node is not None:
                node.unresolved_references.append(edge)

    while queue:
        current = queue.popleft()
        depth = visited[current]
        collect_unresolved(current)
        neighbors: list[tuple[str, RelationshipRecord]] = []
        if direction in (Direction.OUT, Direction.BOTH):
            for rel in out_index.get(current, []):
                if (
                    not matches(rel)
                    or rel.resolution_status is not ResolutionStatus.RESOLVED_STATIC
                ):
                    continue
                if rel.target_symbol_id is None or rel.target_symbol_id not in symbol_by_id:
                    continue
                neighbors.append((rel.target_symbol_id, rel))
        if direction in (Direction.IN, Direction.BOTH):
            for rel in in_index.get(current, []):
                if not matches(rel) or rel.source_symbol_id is None:
                    continue
                if rel.resolution_status is not ResolutionStatus.RESOLVED_STATIC:
                    continue
                if rel.source_symbol_id not in symbol_by_id:
                    continue
                neighbors.append((rel.source_symbol_id, rel))
        neighbors.sort(key=lambda item: (item[1].rel_type.value, _sort_key(item[1]), item[0]))
        if depth >= depth_cap:
            if neighbors:
                depth_capped = True
            continue

        for neighbor_id, rel in neighbors:
            edge = _edge(rel)
            key = _edge_key(edge)
            if neighbor_id in visited:
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append(edge)
                # In BOTH traversal an edge is visible from both indexes; do not
                # reinterpret the immediate backtrack over the same relationship
                # as a two-node cycle.
                if parent_edge.get(current) is rel:
                    continue
                if _is_ancestor(neighbor_id, current, parent):
                    cycle = _cycle_path(neighbor_id, current, parent)
                    canonical = _canonical_cycle(cycle)
                    if canonical not in seen_cycles:
                        if len(cycles) >= MAX_CYCLES:
                            cycle_capped = True
                        else:
                            seen_cycles.add(canonical)
                            cycles.append(cycle)
                continue
            if len(visited) >= node_cap:
                node_capped = True
                continue
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append(edge)
            visited[neighbor_id] = depth + 1
            parent[neighbor_id] = current
            parent_edge[neighbor_id] = rel
            target = symbol_by_id[neighbor_id]
            node = TraceNode(
                symbol_id=target.id,
                qualified_name=target.qualified_name,
                kind=target.kind,
                rel_path=target.rel_path,
                start_line=target.start_line,
                depth=depth + 1,
                via=edge,
            )
            nodes.append(node)
            nodes_by_id[target.id] = node
            queue.append(target.id)

    notes = [
        "Only resolved_static relationships are traversed; unresolved, external,"
        " ambiguous and dynamic references are reported at their source node.",
        "A trace is a static relationship path, not a runtime execution path.",
        f"Traversal bounded to max_depth={depth_cap} and max_nodes={node_cap}.",
    ]
    if direction is Direction.IN:
        notes.append("IN traversal follows reverse edges; each edge keeps its stored direction.")
    if direction is Direction.BOTH:
        notes.append(
            "BOTH traversal may reach a node from either direction; cycle reporting is"
            " traversal-order dependent, and architecture cycles remain authoritative."
        )
    if depth_capped:
        notes.append("max_depth cap reached with further relationships; the result is incomplete")
    if node_capped:
        notes.append("visited-node cap reached; the result is incomplete")
    if cycle_capped:
        notes.append("cycle reporting cap reached; the cycle list is incomplete")
    if cycles:
        notes.append("cycles are reported in traversal direction and deduplicated by rotation")

    return TraceResult(
        repository_id=repository_id,
        root_symbol_id=root.id,
        direction=direction,
        max_depth=depth_cap,
        max_nodes=node_cap,
        nodes=nodes,
        edges=edges,
        unresolved=unresolved,
        cycles=cycles,
        truncated=depth_capped or node_capped or cycle_capped,
        notes=notes,
    )


def trace_file_dependents(
    repository_id: str,
    root_module: SymbolRecord,
    symbols: list[SymbolRecord],
    relationships: list[RelationshipRecord],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> TraceResult:
    """Reverse import traversal at file granularity.

    A ``from module import name`` relationship targets ``name``, not the module
    symbol, so a symbol-level reverse trace on the module would miss it. This
    function maps every resolved import to its **target file** and walks the
    reverse file graph. Each visited file is represented by its module symbol.
    """
    depth_cap = max(1, min(max_depth, HARD_MAX_DEPTH))
    node_cap = max(1, min(max_nodes, HARD_MAX_NODES))
    module_by_file = {
        symbol.file_id: symbol for symbol in symbols if symbol.kind is SymbolKind.MODULE
    }
    symbol_by_id = {symbol.id: symbol for symbol in symbols}
    reverse: dict[str, list[tuple[str, RelationshipRecord]]] = {}
    for rel in relationships:
        if rel.rel_type is not RelationshipType.IMPORTS:
            continue
        if rel.resolution_status is not ResolutionStatus.RESOLVED_STATIC:
            continue
        if not rel.target_symbol_id:
            continue
        target = symbol_by_id.get(rel.target_symbol_id)
        if target is None or target.file_id == rel.source_file_id:
            continue
        reverse.setdefault(target.file_id, []).append((rel.source_file_id, rel))
    for file_key in reverse:
        reverse[file_key].sort(key=lambda item: _sort_key(item[1]))

    root_node = TraceNode(
        symbol_id=root_module.id,
        qualified_name=root_module.qualified_name,
        kind=root_module.kind,
        rel_path=root_module.rel_path,
        start_line=root_module.start_line,
        depth=0,
    )
    nodes: list[TraceNode] = [root_node]
    edges: list[TraceEdge] = []
    seen_edges: set[tuple[str, str, str, int, int, str]] = set()
    cycles: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()
    visited: dict[str, int] = {root_module.file_id: 0}
    parent: dict[str, str] = {}
    queue: deque[str] = deque([root_module.file_id])
    depth_capped = False
    node_capped = False
    cycle_capped = False
    while queue:
        current_file = queue.popleft()
        depth = visited[current_file]
        neighbors = [
            (source_file_id, rel)
            for source_file_id, rel in reverse.get(current_file, [])
            if source_file_id in module_by_file
        ]
        if depth >= depth_cap:
            if neighbors:
                depth_capped = True
            continue
        for source_file_id, rel in neighbors:
            edge = _edge(rel)
            edge_key = _edge_key(edge)
            if source_file_id in visited:
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(edge)
                if _is_ancestor(source_file_id, current_file, parent):
                    cycle = _cycle_path(source_file_id, current_file, parent)
                    canonical = _canonical_cycle(cycle)
                    if canonical not in seen_cycles:
                        if len(cycles) >= MAX_CYCLES:
                            cycle_capped = True
                        else:
                            seen_cycles.add(canonical)
                            mapped = [
                                module_by_file[file_id].id
                                for file_id in cycle
                                if file_id in module_by_file
                            ]
                            cycles.append(mapped)
                continue
            if len(visited) >= node_cap:
                node_capped = True
                continue
            source_module = module_by_file[source_file_id]
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append(edge)
            visited[source_file_id] = depth + 1
            parent[source_file_id] = current_file
            nodes.append(
                TraceNode(
                    symbol_id=source_module.id,
                    qualified_name=source_module.qualified_name,
                    kind=source_module.kind,
                    rel_path=source_module.rel_path,
                    start_line=source_module.start_line,
                    depth=depth + 1,
                    via=edge,
                )
            )
            queue.append(source_file_id)

    notes = [
        "Reverse import trace at file granularity; each node is the module symbol of a file.",
        "Only resolved_static imports are traversed; external, unresolved, ambiguous"
        " and dynamic imports cannot be attributed to a target file.",
        f"Traversal bounded to max_depth={depth_cap} and max_nodes={node_cap}.",
    ]
    if depth_capped:
        notes.append("max_depth cap reached with further imports; the result is incomplete")
    if node_capped:
        notes.append("visited-node cap reached; the result is incomplete")
    if cycle_capped:
        notes.append("cycle reporting cap reached; the cycle list is incomplete")
    return TraceResult(
        repository_id=repository_id,
        root_symbol_id=root_module.id,
        direction=Direction.IN,
        max_depth=depth_cap,
        max_nodes=node_cap,
        nodes=nodes,
        edges=edges,
        unresolved=[],
        cycles=cycles,
        truncated=depth_capped or node_capped or cycle_capped,
        notes=notes,
    )


def connect_symbols(
    repository_id: str,
    source: SymbolRecord,
    target: SymbolRecord,
    symbols: list[SymbolRecord],
    relationships: list[RelationshipRecord],
    *,
    direction: Direction = Direction.OUT,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> TraceConnection:
    """Bounded BFS for a static relationship path between two symbols."""
    depth_cap = max(1, min(max_depth, HARD_MAX_DEPTH))
    node_cap = max(1, min(max_nodes, HARD_MAX_NODES))
    if source.id == target.id:
        return TraceConnection(
            found=True,
            source_symbol_id=source.id,
            target_symbol_id=target.id,
            nodes=[source.id],
            edges=[],
            length=0,
        )
    symbol_by_id = {s.id: s for s in symbols}
    out_index, in_index = _index_relationships(relationships)
    visited: dict[str, int] = {source.id: 0}
    parent: dict[str, tuple[str, RelationshipRecord]] = {}
    queue: deque[str] = deque([source.id])
    depth_capped = False
    node_capped = False
    while queue:
        current = queue.popleft()
        depth = visited[current]
        neighbors: list[tuple[str, RelationshipRecord]] = []
        if direction in (Direction.OUT, Direction.BOTH):
            for rel in out_index.get(current, []):
                if rel.resolution_status is not ResolutionStatus.RESOLVED_STATIC:
                    continue
                if rel.target_symbol_id and rel.target_symbol_id in symbol_by_id:
                    neighbors.append((rel.target_symbol_id, rel))
        if direction in (Direction.IN, Direction.BOTH):
            for rel in in_index.get(current, []):
                if rel.resolution_status is not ResolutionStatus.RESOLVED_STATIC:
                    continue
                if rel.source_symbol_id and rel.source_symbol_id in symbol_by_id:
                    neighbors.append((rel.source_symbol_id, rel))
        neighbors.sort(key=lambda item: (item[1].rel_type.value, _sort_key(item[1]), item[0]))
        if depth >= depth_cap:
            if any(neighbor_id not in visited for neighbor_id, _rel in neighbors):
                depth_capped = True
            continue
        for neighbor_id, rel in neighbors:
            if neighbor_id in visited:
                continue
            if len(visited) >= node_cap:
                node_capped = True
                continue
            visited[neighbor_id] = depth + 1
            parent[neighbor_id] = (current, rel)
            if neighbor_id == target.id:
                chain = [target.id]
                edges: list[TraceEdge] = []
                while chain[-1] != source.id:
                    previous, edge_rel = parent[chain[-1]]
                    edges.append(_edge(edge_rel))
                    chain.append(previous)
                chain.reverse()
                edges.reverse()
                return TraceConnection(
                    found=True,
                    source_symbol_id=source.id,
                    target_symbol_id=target.id,
                    nodes=chain,
                    edges=edges,
                    length=len(chain) - 1,
                )
            queue.append(neighbor_id)
    reason = (
        "visited-node cap reached"
        if node_capped
        else (
            f"max_depth={depth_cap} reached before the target was found"
            if depth_capped
            else f"no static relationship path within max_depth={depth_cap}"
        )
    )
    return TraceConnection(
        found=False,
        source_symbol_id=source.id,
        target_symbol_id=target.id,
        reason=reason,
        truncated=depth_capped or node_capped,
    )
