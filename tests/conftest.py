"""Pytest fixtures and helpers for RepoLens tests.

Provides isolated temporary databases, an app-configured ``TestClient``, and
helpers for registering and analyzing the fixture repositories A-D.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from repolens.config import Settings
from repolens.main import create_app
from repolens.persistence.database import Database
from repolens.services.repository_service import RepositoryService

FIXTURE_REPOS_ROOT = Path(__file__).parent / "fixtures" / "repositories"


def fixture_repo(name: str) -> Path:
    """Absolute path to fixture repository A, B, C or D."""
    return (FIXTURE_REPOS_ROOT / name).resolve()


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "repolens.db"


@pytest.fixture
def settings(tmp_db_path: Path) -> Settings:
    return Settings(db_path=tmp_db_path)


@pytest.fixture
def db(settings: Settings) -> Database:
    database = Database(settings.db_path)
    database.connect()
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def repo_svc(db: Database, settings: Settings) -> RepositoryService:
    return RepositoryService(db, settings)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def register_repo(client: TestClient, name: str, path: Path | None = None) -> str:
    """Register a repository by fixture name and return its id."""
    response = client.post(
        "/api/repositories",
        json={"name": name, "path": str(path or fixture_repo(name))},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def analyze(client: TestClient, repo_id: str) -> dict:
    """Trigger analysis and return the completed run payload."""
    response = client.post(f"/api/repositories/{repo_id}/analysis")
    assert response.status_code == 201, response.text
    return response.json()
