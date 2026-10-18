"""Graph endpoints: node listing, edges, neighbors, path search and stats.

The directed graph is rebuilt from persisted facts on each request (see
``docs/limitations.md``). Only relationships with ``resolved_static`` status
become edges; unresolved references are never materialised as edges. Neighbor
and path lookups resolve the owning repository from the node id itself, so the
URLs match the documented surface (``/api/graph/...``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from repolens.api.deps import get_analysis_service, get_settings
from repolens.config import Settings
from repolens.core.errors import GraphNodeNotFoundError
from repolens.core.models import (
    Direction,
    GraphNode,
    GraphStats,
    NeighborResult,
    PageMeta,
    PaginatedEdges,
    PaginatedNodes,
    PathResult,
)
from repolens.graph.builder import GraphBuilder
from repolens.graph.queries import GraphQuery
from repolens.services.analysis_service import AnalysisService

router = APIRouter()


def _query(repo_id: str, svc: AnalysisService, settings: Settings) -> GraphQuery:
    """Rebuild the graph for one repository from its persisted facts."""
    files, symbols, relationships = svc.load_graph_facts(repo_id)
    graph = GraphBuilder(repo_id, files, symbols, relationships).build()
    return GraphQuery(
        graph,
        max_depth=settings.path_max_depth,
        max_visited=settings.path_max_nodes,
    )


@router.get(
    "/repositories/{repository_id}/graph/stats",
    response_model=GraphStats,
    summary="Node and edge counts for the latest completed run",
)
def graph_stats(
    repository_id: str,
    svc: AnalysisService = Depends(get_analysis_service),
    settings: Settings = Depends(get_settings),
) -> GraphStats:
    return _query(repository_id, svc, settings).stats(repository_id)


@router.get(
    "/repositories/{repository_id}/graph/nodes",
    response_model=PaginatedNodes,
    summary="List graph nodes",
)
def graph_nodes(
    repository_id: str,
    type: Annotated[str | None, Query(description="Filter by node type")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: AnalysisService = Depends(get_analysis_service),
    settings: Settings = Depends(get_settings),
) -> PaginatedNodes:
    window, total = _query(repository_id, svc, settings).nodes(
        node_type=type, limit=limit, offset=offset
    )
    return PaginatedNodes(items=window, page=PageMeta(total=total, limit=limit, offset=offset))


@router.get(
    "/repositories/{repository_id}/graph/edges",
    response_model=PaginatedEdges,
    summary="List graph edges (resolved relationships only)",
)
def graph_edges(
    repository_id: str,
    type: Annotated[str | None, Query(description="Filter by edge type")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: AnalysisService = Depends(get_analysis_service),
    settings: Settings = Depends(get_settings),
) -> PaginatedEdges:
    query = _query(repository_id, svc, settings)
    total = query.g.number_of_edges()
    all_edges = query.edges(edge_type=type, limit=max(total, 1))
    total = len(all_edges)
    return PaginatedEdges(
        items=all_edges[offset : offset + limit],
        page=PageMeta(total=total, limit=limit, offset=offset),
    )


@router.get(
    "/graph/nodes/{node_id:path}/neighbors",
    response_model=NeighborResult,
    summary="Adjacent nodes and edges of a graph node",
)
def graph_neighbors(
    node_id: str,
    edge_type: Annotated[str | None, Query(description="Filter by edge type")] = None,
    direction: Annotated[Direction, Query(description="in, out or both")] = Direction.BOTH,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    svc: AnalysisService = Depends(get_analysis_service),
    settings: Settings = Depends(get_settings),
) -> NeighborResult:
    repo_id = svc.find_repository_for_node(node_id)
    if repo_id is None:
        raise GraphNodeNotFoundError(node_id)
    query = _query(repo_id, svc, settings)
    if not query.has_node(node_id):
        raise GraphNodeNotFoundError(node_id)
    node, neighbors, edges = query.neighbors(
        node_id, edge_type=edge_type, direction=direction, limit=limit
    )
    return NeighborResult(node=node, neighbors=neighbors, edges=edges)


@router.get(
    "/graph/path",
    response_model=PathResult,
    summary="Shortest directed path between two nodes",
    description=(
        "Breadth-first search bounded by depth and a visited-node cap. When no "
        "path is found the result reports ``found=false`` with a reason; a path "
        "is never fabricated."
    ),
)
def graph_path(
    source: Annotated[str, Query(description="Source node id")],
    target: Annotated[str, Query(description="Target node id")],
    max_depth: Annotated[int | None, Query(ge=1, le=10)] = None,
    svc: AnalysisService = Depends(get_analysis_service),
    settings: Settings = Depends(get_settings),
) -> PathResult:
    repo_id = svc.find_repository_for_node(source) or svc.find_repository_for_node(target)
    if repo_id is None:
        return PathResult(
            found=False,
            source=source,
            target=target,
            reason="source node does not exist",
        )
    return _query(repo_id, svc, settings).path(source, target, max_depth=max_depth)


@router.get(
    "/graph/nodes/{node_id:path}",
    response_model=GraphNode,
    summary="Single graph node by id",
)
def get_graph_node(
    node_id: str,
    svc: AnalysisService = Depends(get_analysis_service),
    settings: Settings = Depends(get_settings),
) -> GraphNode:
    repo_id = svc.find_repository_for_node(node_id)
    if repo_id is None:
        raise GraphNodeNotFoundError(node_id)
    node = _query(repo_id, svc, settings).node(node_id)
    if node is None:
        raise GraphNodeNotFoundError(node_id)
    return node
