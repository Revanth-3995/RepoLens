"""File-level endpoints: listing, per-file detail, and source access."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from repolens.api.deps import get_analysis_service
from repolens.core.models import (
    FileDetail,
    PaginatedFiles,
    PaginatedRelationships,
    RelationshipType,
    ResolutionStatus,
)
from repolens.services.analysis_service import AnalysisService

router = APIRouter()


@router.get(
    "/repositories/{repository_id}/files",
    response_model=PaginatedFiles,
    summary="List analyzed files of the latest completed run",
)
def list_files(
    repository_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: AnalysisService = Depends(get_analysis_service),
) -> PaginatedFiles:
    return svc.list_files(repository_id, limit=limit, offset=offset)


@router.get(
    "/repositories/{repository_id}/files/{rel_path:path}",
    response_model=FileDetail,
    summary="File detail, optionally with bounded source text",
    description=(
        "``rel_path`` is a repository-relative POSIX path. Source text is "
        "re-read from disk with root-boundary checks and is never stored in "
        "the database; ``start_line``/``end_line`` bound the returned slice."
    ),
)
def get_file(
    repository_id: str,
    rel_path: str,
    include_source: Annotated[bool, Query(description="Re-read and return source text")] = False,
    start_line: Annotated[int | None, Query(ge=1, description="1-based inclusive start")] = None,
    end_line: Annotated[int | None, Query(ge=1, description="1-based inclusive end")] = None,
    svc: AnalysisService = Depends(get_analysis_service),
) -> FileDetail:
    return svc.get_file_detail(
        repository_id,
        rel_path,
        include_source=include_source,
        start_line=start_line,
        end_line=end_line,
    )


@router.get(
    "/repositories/{repository_id}/relationships",
    response_model=PaginatedRelationships,
    summary="List resolved and unresolved relationships",
    description=(
        "Every relationship carries an explicit resolution status: "
        "``resolved_static``, ``unresolved``, ``ambiguous``, ``external`` or "
        "``dynamic_or_unknown``. Only ``resolved_static`` relationships "
        "become graph edges."
    ),
)
def list_relationships(
    repository_id: str,
    rel_type: Annotated[
        RelationshipType | None, Query(description="Filter by relationship type")
    ] = None,
    status: Annotated[
        ResolutionStatus | None, Query(description="Filter by resolution status")
    ] = None,
    symbol_id: Annotated[str | None, Query(description="Filter by source or target symbol")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: AnalysisService = Depends(get_analysis_service),
) -> PaginatedRelationships:
    return svc.list_relationships(
        repository_id,
        rel_type=rel_type,
        status=status,
        symbol_id=symbol_id,
        limit=limit,
        offset=offset,
    )
