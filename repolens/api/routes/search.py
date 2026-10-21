"""Lexical search endpoint (Phase 2, P2-05)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from repolens.api.deps import get_search_service
from repolens.core.models import PaginatedSearchHits, SearchMode, SymbolKind
from repolens.services.search_service import SearchService

router = APIRouter()


@router.get(
    "/repositories/{repository_id}/search",
    response_model=PaginatedSearchHits,
    summary="Search symbols and source text of the latest completed run",
    description=(
        "Deterministic lexical search only (no embeddings). modes: all"
        " (symbols + substring text), symbol, identifier, text, token"
        " (whole-word source tokens). Ranking is an integer score documented in"
        " docs/api.md; ties are stable by path/line/kind/name/snippet. Empty queries are"
        " rejected with 400."
    ),
)
def search(
    repository_id: str,
    q: Annotated[str, Query(min_length=1, max_length=500, description="Search query")],
    mode: SearchMode = SearchMode.ALL,
    kind: Annotated[SymbolKind | None, Query(description="Restrict to one symbol kind")] = None,
    path_prefix: Annotated[
        str | None, Query(max_length=4096, description="Restrict to a path prefix")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: SearchService = Depends(get_search_service),
) -> PaginatedSearchHits:
    return svc.search(
        repository_id,
        q,
        mode=mode,
        kind=kind,
        path_prefix=path_prefix,
        limit=limit,
        offset=offset,
    )
