"""T3: repository registration, path validation and lookups."""

from __future__ import annotations

from pathlib import Path

import pytest

from repolens.core.errors import (
    InvalidRepositoryPathError,
    RepositoryExistsError,
    RepositoryNotFoundError,
)
from repolens.services.repository_service import RepositoryService


def test_register_existing_directory(repo_svc: RepositoryService, tmp_path: Path):
    repo = repo_svc.register("demo", str(tmp_path))
    assert repo.id.startswith("repo_")
    assert repo.name == "demo"
    assert Path(repo.root_path) == tmp_path.resolve()


def test_register_rejects_missing_path(repo_svc: RepositoryService, tmp_path: Path):
    with pytest.raises(InvalidRepositoryPathError):
        repo_svc.register("demo", str(tmp_path / "does-not-exist"))


def test_register_rejects_file_path(repo_svc: RepositoryService, tmp_path: Path):
    file_path = tmp_path / "a.py"
    file_path.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(InvalidRepositoryPathError):
        repo_svc.register("demo", str(file_path))


def test_duplicate_registration_rejected(repo_svc: RepositoryService, tmp_path: Path):
    repo_svc.register("demo", str(tmp_path))
    with pytest.raises(RepositoryExistsError):
        repo_svc.register("demo-again", str(tmp_path))


def test_list_and_get(repo_svc: RepositoryService, tmp_path: Path):
    created = repo_svc.register("demo", str(tmp_path))
    listed = repo_svc.list()
    assert [r.id for r in listed] == [created.id]
    fetched = repo_svc.get(created.id)
    assert fetched.id == created.id
    assert fetched.latest_run is None


def test_get_unknown_raises(repo_svc: RepositoryService):
    with pytest.raises(RepositoryNotFoundError):
        repo_svc.get("repo_missing")


def test_get_root_returns_registered_path(repo_svc: RepositoryService, tmp_path: Path):
    created = repo_svc.register("demo", str(tmp_path))
    assert repo_svc.get_root(created.id) == tmp_path.resolve()


def test_repo_id_is_stable_for_same_path(repo_svc: RepositoryService, tmp_path: Path):
    first = repo_svc.register("demo", str(tmp_path))
    # Re-registering the same path is rejected, but the derived id is stable.
    from repolens.core.identifiers import repo_id_for

    assert repo_id_for(str(tmp_path)) == first.id
