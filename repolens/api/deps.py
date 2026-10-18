"""FastAPI dependency accessors for application services."""

from __future__ import annotations

from fastapi import Request

from repolens.config import Settings
from repolens.services.analysis_service import AnalysisService
from repolens.services.repository_service import RepositoryService


def get_repository_service(request: Request) -> RepositoryService:
    return request.app.state.repository_service


def get_analysis_service(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
