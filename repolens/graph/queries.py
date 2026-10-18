"""Bounded graph queries: neighbors, typed edges, statistics, shortest paths.

Path search (AD-14): iterative BFS bounded by ``max_depth`` (default from
settings, hard cap 10) and a visited-node cap (``path_max_nodes``). When no
path is found the result says so explicitly — a path is never fabricated.
"""

from __future__ import annotations

from collections import deque
from typing import Any

import networkx as nx

from repolens.core.models import (
    Direction,
    GraphEdge,
    GraphEdgeSite,
    GraphNode,
    GraphNodeType,
    GraphStats,
    PathResult,
)


def _node(g: nx.MultiDiGraph, node_id: str) -> GraphNode:
    data = dict(g.nodes[node_id])
    ntype = data.pop("type", "file")
    label = data.pop("label", node_id)
    return GraphNode(id=node_id, type=GraphNodeType(ntype), label=label, attrs=data)


def _edge(src: str, tgt: str, data: dict[str, Any]) -> GraphEdge:
    sites = [GraphEdgeSite.model_validate(s) for s in data.get("sites", [])]
    return GraphEdge(source=src, target=tgt, type=data.get("type", ""), sites=sites)


class GraphQuery:
    """Query facade over a built NetworkX MultiDiGraph."""

    def __init__(
        self, g: nx.MultiDiGraph, *, max_depth: int = 6, max_visited: int = 20_000
    ) -> None:
        self.g = g
        self.max_depth = max(1, min(max_depth, 10))
        self.max_visited = max(100, max_visited)

    def has_node(self, node_id: str) -> bool:
        return node_id in self.g

    def node(self, node_id: str) -> GraphNode | None:
        if node_id not in self.g:
            return None
        return _node(self.g, node_id)

    def nodes(
        self, *, node_type: str | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[list[GraphNode], int]:
        """Return (window, total) of graph nodes honoring an optional type filter."""
        all_nodes = [
            _node(self.g, nid)
            for nid, data in self.g.nodes(data=True)
            if node_type is None or data.get("type") == node_type
        ]
        return all_nodes[offset : offset + limit], len(all_nodes)

    def neighbors(
        self,
        node_id: str,
        *,
        edge_type: str | None = None,
        direction: Direction = Direction.BOTH,
        limit: int = 100,
    ) -> tuple[GraphNode, list[GraphNode], list[GraphEdge]]:
        """Return (node, adjacent nodes, edges) honoring filters and limit."""
        root = _node(self.g, node_id)
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        seen: set[Any] = set()

        def collect(neighbor: str, src: str, tgt: str, data: dict[str, Any]) -> None:
            if edge_type and data.get("type") != edge_type:
                return
            # Dedupe by (src, tgt, type) so parallel edges of different types
            # all survive while identical (src, tgt, type) pairs stay merged.
            key = (src, tgt, data.get("type"))
            if key in seen:
                return
            seen.add(key)
            nodes.append(_node(self.g, neighbor))
            edges.append(_edge(src, tgt, data))

        if direction in (Direction.OUT, Direction.BOTH):
            for nbr in self.g.successors(node_id):
                if len(edges) >= limit:
                    break
                edata = self.g.get_edge_data(node_id, nbr) or {}
                for _key, data in edata.items():
                    if len(edges) >= limit:
                        break
                    collect(nbr, node_id, nbr, data)
        if direction in (Direction.IN, Direction.BOTH):
            for nbr in self.g.predecessors(node_id):
                if len(edges) >= limit:
                    break
                edata = self.g.get_edge_data(nbr, node_id) or {}
                for _key, data in edata.items():
                    if len(edges) >= limit:
                        break
                    collect(nbr, nbr, node_id, data)
        return root, nodes, edges

    def edges(self, *, edge_type: str | None = None, limit: int = 100) -> list[GraphEdge]:
        out: list[GraphEdge] = []
        for src, tgt, data in self.g.edges(data=True):
            if edge_type and data.get("type") != edge_type:
                continue
            out.append(_edge(src, tgt, data))
            if len(out) >= limit:
                break
        return out

    def stats(self, repo_id: str) -> GraphStats:
        by_node: dict[str, int] = {}
        by_edge: dict[str, int] = {}
        for _, data in self.g.nodes(data=True):
            t = str(data.get("type", "?"))
            by_node[t] = by_node.get(t, 0) + 1
        for _, _, data in self.g.edges(data=True):
            t = str(data.get("type", "?"))
            by_edge[t] = by_edge.get(t, 0) + 1
        return GraphStats(
            repository_id=repo_id,
            nodes=self.g.number_of_nodes(),
            edges=self.g.number_of_edges(),
            by_node_type=by_node,
            by_edge_type=by_edge,
        )

    def path(self, source: str, target: str, max_depth: int | None = None) -> PathResult:
        if source not in self.g:
            return PathResult(
                found=False,
                source=source,
                target=target,
                reason="source node does not exist",
            )
        if target not in self.g:
            return PathResult(
                found=False,
                source=source,
                target=target,
                reason="target node does not exist",
            )
        if source == target:
            return PathResult(found=True, source=source, target=target, nodes=[source], length=0)

        depth_cap = min(max_depth if max_depth is not None else self.max_depth, 10)
        visited: set[str] = {source}
        parent: dict[str, str] = {}
        queue: deque[tuple[str, int]] = deque([(source, 0)])
        hit_cap = False

        while queue:
            if len(visited) > self.max_visited:
                hit_cap = True
                break
            current, depth = queue.popleft()
            if depth >= depth_cap:
                continue
            for nbr in self.g.successors(current):
                if nbr in visited:
                    continue
                visited.add(nbr)
                parent[nbr] = current
                if nbr == target:
                    # reconstruct
                    chain = [nbr]
                    while chain[-1] != source:
                        chain.append(parent[chain[-1]])
                    chain.reverse()
                    return PathResult(
                        found=True,
                        source=source,
                        target=target,
                        nodes=chain,
                        length=len(chain) - 1,
                    )
                queue.append((nbr, depth + 1))

        if hit_cap:
            reason = f"search bound exceeded (visited > {self.max_visited} nodes)"
        else:
            reason = f"no path within max_depth={depth_cap}"
        return PathResult(found=False, source=source, target=target, reason=reason)
