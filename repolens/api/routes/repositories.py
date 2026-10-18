"""Repository management endpoints: register, list, retrieve."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from repolens.api.deps import get_repository_service
from repolens.core.models import Repository
from repolens.services.repository_service import RepositoryService

router = APIRouter()


class RegisterRepositoryRequest(BaseModel):
    """Request body for repository registration."""

    name: str = Field(min_length=1, max_length=200, description="Display name")
    path: str = Field(min_length=1, max_length=4096, description="Absolute filesystem path")


@router.post(
    "/repositories",
    response_model=Repository,
    status_code=status.HTTP_201_CREATED,
    summary="Register a local repository by absolute path",
)
def register_repository(
    body: RegisterRepositoryRequest,
    svc: RepositoryService = Depends(get_repository_service),
) -> Repository:
    return svc.register(body.name, body.path)


@router.get(
    "/repositories", response_model=list[Repository], summary="List registered repositories"
)
def list_repositories(
    svc: RepositoryService = Depends(get_repository_service),
) -> list[Repository]:
    return svc.list()


@router.get(
    "/repositories/{repository_id}",
    response_model=Repository,
    summary="Repository metadata with latest analysis run",
)
def get_repository(
    repository_id: str,
    svc: RepositoryService = Depends(get_repository_service),
) -> Repository:
    return svc.get(repository_id)
