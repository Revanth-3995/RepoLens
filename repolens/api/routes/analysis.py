"""Analysis endpoints: trigger runs, inspect runs, errors, and summaries."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from repolens.api.deps import get_analysis_service
from repolens.core.models import (
    AnalysisRun,
    PaginatedIssues,
)
from repolens.services.analysis_service import AnalysisService

router = APIRouter()


@router.post(
    "/repositories/{repository_id}/analysis",
    response_model=AnalysisRun,
    status_code=status.HTTP_201_CREATED,
    summary="Trigger a synchronous analysis of the repository",
    description=(
        "Discovers, reads, parses and resolves the repository's files, then "
        "persists the results atomically. Returns the completed run with its "
        "summary; failed runs are recorded and reported with 500."
    ),
)
def trigger_analysis(
    repository_id: str,
    svc: AnalysisService = Depends(get_analysis_service),
) -> AnalysisRun:
    return svc.trigger(repository_id)


@router.get(
    "/repositories/{repository_id}/runs",
    response_model=list[AnalysisRun],
    summary="List analysis runs (newest first)",
)
def list_runs(
    repository_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    svc: AnalysisService = Depends(get_analysis_service),
) -> list[AnalysisRun]:
    return svc.list_runs(repository_id, limit=limit)


@router.get(
    "/analysis/runs/{run_id}",
    response_model=AnalysisRun,
    summary="Single analysis run by id",
)
def get_run(
    run_id: str,
    svc: AnalysisService = Depends(get_analysis_service),
) -> AnalysisRun:
    return svc.get_run(run_id)


@router.get(
    "/analysis/runs/{run_id}/errors",
    response_model=PaginatedIssues,
    summary="Read/parse errors recorded for a run",
)
def get_run_errors(
    run_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: AnalysisService = Depends(get_analysis_service),
) -> PaginatedIssues:
    return svc.get_errors(run_id, limit=limit, offset=offset)
