"""Route registry: one APIRouter aggregating all endpoint modules."""

from __future__ import annotations

from fastapi import APIRouter

from repolens.api.routes import (
    analysis,
    architecture,
    cfg,
    explanations,
    files,
    graph,
    metrics,
    repositories,
    search,
    symbols,
    trace,
)

api_router = APIRouter()
api_router.include_router(repositories.router, tags=["repositories"])
api_router.include_router(analysis.router, tags=["analysis"])
api_router.include_router(files.router, tags=["files"])
api_router.include_router(symbols.router, tags=["symbols"])
api_router.include_router(metrics.router, tags=["metrics"])
api_router.include_router(cfg.router, tags=["comprehension"])
api_router.include_router(explanations.router, tags=["explanations"])
api_router.include_router(search.router, tags=["search"])
api_router.include_router(trace.router, tags=["trace"])
api_router.include_router(architecture.router, tags=["architecture"])
api_router.include_router(graph.router, tags=["graph"])
