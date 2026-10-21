"""Deterministic explanation endpoints (Phase 2, P2-04)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from repolens.api.deps import get_comprehension_service
from repolens.core.models import Explanation
from repolens.services.comprehension_service import ComprehensionService

router = APIRouter()


@router.get(
    "/repositories/{repository_id}/explanations/function/{symbol_id}",
    response_model=Explanation,
    summary="Explain one function from metrics, CFG, data flow, and calls",
)
def explain_function(
    repository_id: str,
    symbol_id: str,
    svc: ComprehensionService = Depends(get_comprehension_service),
) -> Explanation:
    # The service checks the repository run and verifies symbol ownership before
    # explaining it.
    return svc.function_explanation(repository_id, symbol_id)


@router.get(
    "/repositories/{repository_id}/explanations/statement/{symbol_id}",
    response_model=Explanation,
    summary="Explain the statement covering one source line of a function",
)
def explain_statement(
    repository_id: str,
    symbol_id: str,
    line: Annotated[int, Query(ge=1, description="One-based source line")],
    svc: ComprehensionService = Depends(get_comprehension_service),
) -> Explanation:
    return svc.statement_explanation(repository_id, symbol_id, line)


@router.get(
    "/repositories/{repository_id}/explanations/file/{rel_path:path}",
    response_model=Explanation,
    summary="Explain one analyzed file",
)
def explain_file(
    repository_id: str,
    rel_path: str,
    svc: ComprehensionService = Depends(get_comprehension_service),
) -> Explanation:
    return svc.file_explanation(repository_id, rel_path)


@router.get(
    "/repositories/{repository_id}/explanations/folder",
    response_model=Explanation,
    summary="Explain a path-based folder grouping",
)
def explain_folder(
    repository_id: str,
    prefix: Annotated[str, Query(max_length=4096, description="Folder path prefix")] = "",
    svc: ComprehensionService = Depends(get_comprehension_service),
) -> Explanation:
    return svc.folder_explanation(repository_id, prefix)


@router.get(
    "/repositories/{repository_id}/explanations/project",
    response_model=Explanation,
    summary="Explain the analyzed repository",
)
def explain_project(
    repository_id: str,
    svc: ComprehensionService = Depends(get_comprehension_service),
) -> Explanation:
    return svc.project_explanation(repository_id)
