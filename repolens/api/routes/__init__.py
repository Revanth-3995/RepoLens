"""Route registry: one APIRouter aggregating all endpoint modules."""

from __future__ import annotations

from fastapi import APIRouter

from repolens.api.routes import analysis, files, graph, repositories, symbols

api_router = APIRouter()
api_router.include_router(repositories.router, tags=["repositories"])
api_router.include_router(analysis.router, tags=["analysis"])
api_router.include_router(files.router, tags=["files"])
api_router.include_router(symbols.router, tags=["symbols"])
api_router.include_router(graph.router, tags=["graph"])
