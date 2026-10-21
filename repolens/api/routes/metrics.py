"""Function-level structural metrics endpoints (Phase 2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from repolens.api.deps import get_analysis_service
from repolens.core.models import PageMeta, PaginatedFunctionMetrics
from repolens.services.analysis_service import AnalysisService

router = APIRouter()


@router.get(
    "/repositories/{repository_id}/metrics/functions",
    response_model=PaginatedFunctionMetrics,
    summary="Function-level structural metrics of the latest completed run",
    description=(
        "Cyclomatic complexity is an AST decision-point approximation:"
        " 1 + if + for + while + except + match-cases + extra boolean operands."
        " It is not a CFG cyclomatic number. Nested function/class/lambda bodies"
        " are excluded from the enclosing function's counts."
    ),
)
def list_function_metrics(
    repository_id: str,
    symbol_id: Annotated[
        str | None, Query(description="Restrict to one function symbol id")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    svc: AnalysisService = Depends(get_analysis_service),
) -> PaginatedFunctionMetrics:
    items, total = svc.list_function_metrics(
        repository_id, limit=limit, offset=offset, symbol_id=symbol_id
    )
    return PaginatedFunctionMetrics(
        items=items, page=PageMeta(total=total, limit=min(limit, 500), offset=offset)
    )
