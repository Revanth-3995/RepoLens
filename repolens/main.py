"""FastAPI application factory and ASGI entry point.

Wires configuration, persistence and services onto ``app.state`` and maps
domain errors to a stable JSON envelope ``{"error": {"code", "detail"}}``.
Run with ``python -m repolens.main`` or
``uvicorn repolens.main:app --reload``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from repolens import __version__
from repolens.api.routes import api_router
from repolens.config import Settings, configure_logging
from repolens.core.errors import (
    AnalysisExecutionError,
    AnalysisNotAvailableError,
    AnalysisRunNotFoundError,
    FileNotFoundError_,
    GraphIntegrityError,
    GraphNodeNotFoundError,
    InvalidRepositoryPathError,
    InvalidRequestError,
    PathTraversalError,
    RepoLensError,
    RepositoryExistsError,
    RepositoryNotFoundError,
    SourceUnavailableError,
    SymbolNotFoundError,
)
from repolens.core.models import HealthResponse
from repolens.persistence.database import Database
from repolens.services.analysis_service import AnalysisService
from repolens.services.architecture_service import ArchitectureService
from repolens.services.comprehension_service import ComprehensionService
from repolens.services.repository_service import RepositoryService
from repolens.services.search_service import SearchService
from repolens.services.trace_service import TraceService

logger = logging.getLogger(__name__)

# HTTP status per domain error (AD: safe, non-leaking error envelope).
_STATUS_BY_ERROR: tuple[tuple[type[RepoLensError], int], ...] = (
    (RepositoryNotFoundError, 404),
    (AnalysisRunNotFoundError, 404),
    (FileNotFoundError_, 404),
    (SymbolNotFoundError, 404),
    (GraphNodeNotFoundError, 404),
    (AnalysisNotAvailableError, 404),
    (RepositoryExistsError, 409),
    (InvalidRepositoryPathError, 400),
    (PathTraversalError, 400),
    (InvalidRequestError, 400),
    (SourceUnavailableError, 409),
    (AnalysisExecutionError, 500),
    (GraphIntegrityError, 500),
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application with services bound to ``app.state``."""
    settings = settings or Settings.from_env()
    configure_logging(settings)

    database = Database(settings.db_path)
    database.connect()  # initialize schema eagerly so failures surface at startup

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            database.close()

    app = FastAPI(
        title="RepoLens",
        version=__version__,
        description=(
            "No-LLM static analysis of local Python repositories. Phase 1 covers "
            "ingestion, AST parsing, conservative symbol/relationship extraction, "
            "a code graph, and SQLite persistence; Phase 2 adds deterministic "
            "metrics, CFGs, local data flow, explanations, search, tracing, and "
            "architecture indicators."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.repository_service = RepositoryService(database, settings)
    app.state.analysis_service = AnalysisService(database, settings)
    app.state.architecture_service = ArchitectureService(app.state.analysis_service, settings)
    app.state.comprehension_service = ComprehensionService(
        app.state.analysis_service, settings, app.state.architecture_service
    )
    app.state.search_service = SearchService(app.state.analysis_service, settings)
    app.state.trace_service = TraceService(app.state.analysis_service, settings)

    app.include_router(api_router, prefix="/api")

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.exception_handler(RepoLensError)
    async def _domain_error_handler(_: Request, exc: RepoLensError) -> JSONResponse:
        status_code = 500
        for exc_type, code in _STATUS_BY_ERROR:
            if isinstance(exc, exc_type):
                status_code = code
                break
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code, "detail": exc.message}},
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # Log the full traceback server-side; never leak it to the client.
        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "detail": "internal server error"}},
        )

    return app


app = create_app()


def main() -> None:  # pragma: no cover - manual entry point
    import uvicorn

    settings = app.state.settings
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover
    main()
