"""Architecture summary and findings endpoints (Phase 2, P2-07)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from repolens.api.deps import get_architecture_service
from repolens.core.models import (
    ArchitectureSummary,
    FindingSeverity,
    PaginatedFindings,
)
from repolens.services.architecture_service import ArchitectureService

router = APIRouter()


@router.get(
    "/repositories/{repository_id}/architecture",
    response_model=ArchitectureSummary,
    summary="Dependency summary, cycles, centrality, coupling, hotspots",
    description=(
        "All metrics and thresholds are explicit in the response. Centrality is"
        " degree centrality on the resolved-import graph; instability is"
        " fan_out/(fan_in+fan_out); complexity is the P2-01 AST approximation."
        " Findings are review indicators, never automatic defect claims."
    ),
)
def architecture(
    repository_id: str,
    svc: ArchitectureService = Depends(get_architecture_service),
) -> ArchitectureSummary:
    return svc.summary(repository_id)


@router.get(
    "/repositories/{repository_id}/architecture/findings",
    response_model=PaginatedFindings,
    summary="Paginated architecture findings",
)
def architecture_findings(
    repository_id: str,
    kind: Annotated[str | None, Query(max_length=100)] = None,
    severity: FindingSeverity | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: ArchitectureService = Depends(get_architecture_service),
) -> PaginatedFindings:
    return svc.findings(
        repository_id,
        kind=kind,
        severity=severity,
        limit=limit,
        offset=offset,
    )
