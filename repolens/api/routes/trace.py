"""Cross-file tracing endpoints (Phase 2, P2-06)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from repolens.api.deps import get_trace_service
from repolens.core.errors import InvalidRequestError
from repolens.core.models import (
    Direction,
    RelationshipType,
    TraceConnection,
    TraceResult,
)
from repolens.services.trace_service import TraceService

router = APIRouter()


def _parse_rel_types(raw: str | None) -> set[RelationshipType] | None:
    if not raw:
        return None
    found: set[RelationshipType] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            found.add(RelationshipType(value))
        except ValueError as exc:
            valid = ", ".join(item.value for item in RelationshipType)
            raise InvalidRequestError(
                f"unknown relationship type {value!r}; valid values: {valid}"
            ) from exc
    return found or None


@router.get(
    "/repositories/{repository_id}/trace",
    response_model=TraceResult,
    summary="Bounded static relationship trace from a symbol",
    description=(
        "Traverses resolved_static relationships only. Unresolved, external,"
        " ambiguous and dynamic references are reported at their source node and"
        " never traversed. direction=out|in|both. This is a static relationship"
        " path, not a runtime execution trace."
    ),
)
def trace(
    repository_id: str,
    symbol_id: Annotated[str, Query(min_length=1)],
    direction: Direction = Direction.OUT,
    rel_types: Annotated[
        str | None, Query(description="Comma-separated: imports,calls,inherits_from")
    ] = None,
    max_depth: Annotated[int | None, Query(ge=1, le=8)] = None,
    max_nodes: Annotated[int | None, Query(ge=1, le=5000)] = None,
    svc: TraceService = Depends(get_trace_service),
) -> TraceResult:
    return svc.trace(
        repository_id,
        symbol_id,
        direction=direction,
        rel_types=_parse_rel_types(rel_types),
        max_depth=max_depth,
        max_nodes=max_nodes,
    )


@router.get(
    "/repositories/{repository_id}/trace/connect",
    response_model=TraceConnection,
    summary="Find a bounded static relationship path between two symbols",
)
def connect(
    repository_id: str,
    source_symbol_id: Annotated[str, Query(min_length=1)],
    target_symbol_id: Annotated[str, Query(min_length=1)],
    direction: Direction = Direction.OUT,
    max_depth: Annotated[int | None, Query(ge=1, le=8)] = None,
    max_nodes: Annotated[int | None, Query(ge=1, le=5000)] = None,
    svc: TraceService = Depends(get_trace_service),
) -> TraceConnection:
    return svc.connect(
        repository_id,
        source_symbol_id,
        target_symbol_id,
        direction=direction,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )


@router.get(
    "/repositories/{repository_id}/dependents",
    response_model=TraceResult,
    summary="Which files depend on this module? (reverse import trace)",
)
def dependents(
    repository_id: str,
    rel_path: Annotated[str, Query(min_length=1, max_length=4096)],
    max_depth: Annotated[int | None, Query(ge=1, le=8)] = None,
    max_nodes: Annotated[int | None, Query(ge=1, le=5000)] = None,
    svc: TraceService = Depends(get_trace_service),
) -> TraceResult:
    return svc.dependents(repository_id, rel_path, max_depth=max_depth, max_nodes=max_nodes)
