"""FastAPI dependency accessors for application services."""

from __future__ import annotations

from fastapi import Request

from repolens.config import Settings
from repolens.services.analysis_service import AnalysisService
from repolens.services.architecture_service import ArchitectureService
from repolens.services.comprehension_service import ComprehensionService
from repolens.services.repository_service import RepositoryService
from repolens.services.search_service import SearchService
from repolens.services.trace_service import TraceService


def get_repository_service(request: Request) -> RepositoryService:
    return request.app.state.repository_service


def get_analysis_service(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


def get_architecture_service(request: Request) -> ArchitectureService:
    return request.app.state.architecture_service


def get_comprehension_service(request: Request) -> ComprehensionService:
    return request.app.state.comprehension_service


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service


def get_trace_service(request: Request) -> TraceService:
    return request.app.state.trace_service


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
