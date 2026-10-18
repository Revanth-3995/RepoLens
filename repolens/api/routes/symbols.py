"""Symbol endpoints: search, detail, parent and children navigation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from repolens.api.deps import get_analysis_service
from repolens.core.models import PaginatedSymbols, SymbolKind, SymbolRecord
from repolens.services.analysis_service import AnalysisService

router = APIRouter()


@router.get(
    "/repositories/{repository_id}/symbols",
    response_model=PaginatedSymbols,
    summary="List symbols of the latest completed run",
)
def list_symbols(
    repository_id: str,
    file_id: Annotated[str | None, Query(description="Restrict to one file node id")] = None,
    kind: Annotated[SymbolKind | None, Query(description="Restrict to one symbol kind")] = None,
    name: Annotated[str | None, Query(description="Exact symbol name match")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: AnalysisService = Depends(get_analysis_service),
) -> PaginatedSymbols:
    return svc.list_symbols(
        repository_id,
        file_id=file_id,
        kind=kind,
        name=name,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/symbols/{symbol_id}",
    response_model=SymbolRecord,
    summary="Single symbol by id (with signature and location)",
)
def get_symbol(
    symbol_id: str,
    svc: AnalysisService = Depends(get_analysis_service),
) -> SymbolRecord:
    return svc.get_symbol(symbol_id)


@router.get(
    "/symbols/{symbol_id}/parent",
    response_model=SymbolRecord | None,
    summary="Enclosing symbol (None for module-level symbols)",
)
def get_symbol_parent(
    symbol_id: str,
    svc: AnalysisService = Depends(get_analysis_service),
) -> SymbolRecord | None:
    return svc.get_symbol_parent(symbol_id)


@router.get(
    "/symbols/{symbol_id}/children",
    response_model=list[SymbolRecord],
    summary="Symbols nested directly inside this symbol",
)
def get_symbol_children(
    symbol_id: str,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    svc: AnalysisService = Depends(get_analysis_service),
) -> list[SymbolRecord]:
    return svc.get_symbol_children(symbol_id, limit=limit)
