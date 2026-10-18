"""Application services: repository registration and analysis orchestration.

- RepositoryService: register/list/get repositories; validate paths.
- AnalysisService: trigger analysis (synchronous), atomic persistence swap of
  the new run's facts, run history, and retrieval of files/symbols/relationships.
"""

from __future__ import annotations

from pathlib import Path

from repolens.config import Settings
from repolens.core.errors import (
    InvalidRepositoryPathError,
    RepositoryExistsError,
    RepositoryNotFoundError,
)
from repolens.core.identifiers import repo_id_for
from repolens.core.models import (
    AnalysisRun,
    Repository,
    RunStatus,
)
from repolens.persistence.database import Database, iso, now
from repolens.persistence.repositories import (
    _row_to_repo,
    _row_to_run,
)


class RepositoryService:
    """Repository registration and metadata access."""

    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings

    def register(self, name: str, path: str) -> Repository:
        conn = self.db.connect()
        root = Path(path).resolve()
        if not root.exists() or not root.is_dir():
            raise InvalidRepositoryPathError(f"Path is not an existing directory: {path}")
        rid = repo_id_for(str(root))
        # check exists
        cur = conn.execute(
            "SELECT id FROM repositories WHERE id = ? OR root_path = ?", (rid, str(root))
        )
        if cur.fetchone():
            raise RepositoryExistsError(f"Repository already registered: {root}")
        registered_at = now()
        conn.execute(
            "INSERT INTO repositories (id, name, root_path, registered_at) VALUES (?, ?, ?, ?)",
            (rid, name, str(root), iso(registered_at)),
        )
        conn.commit()
        return Repository(id=rid, name=name, root_path=str(root), registered_at=registered_at)

    def list(self) -> list[Repository]:
        conn = self.db.connect()
        cur = conn.execute(
            "SELECT id, name, root_path, registered_at FROM repositories ORDER BY registered_at"
        )
        repos = []
        for r in cur.fetchall():
            row = _row_to_repo(r)
            repos.append(
                Repository(
                    id=row.id,
                    name=row.name,
                    root_path=row.root_path,
                    registered_at=row.registered_at,
                )
            )
        return repos

    def get(self, rid: str) -> Repository:
        conn = self.db.connect()
        cur = conn.execute(
            "SELECT id, name, root_path, registered_at FROM repositories WHERE id = ?", (rid,)
        )
        r = cur.fetchone()
        if not r:
            raise RepositoryNotFoundError(rid)
        row = _row_to_repo(r)
        repo = Repository(
            id=row.id, name=row.name, root_path=row.root_path, registered_at=row.registered_at
        )
        # latest run
        cur = conn.execute(
            "SELECT id, repository_id, status, started_at, finished_at, summary_json, error FROM analysis_runs WHERE repository_id = ? ORDER BY started_at DESC LIMIT 1",
            (rid,),
        )
        rr = cur.fetchone()
        if rr:
            rrow = _row_to_run(rr)
            repo.latest_run = AnalysisRun(
                id=rrow.id,
                repository_id=rrow.repository_id,
                status=RunStatus(rrow.status),
                started_at=rrow.started_at,
                finished_at=rrow.finished_at,
                summary=Database.json_to_summary(rrow.summary_json),
                error=rrow.error,
            )
        return repo

    def get_root(self, rid: str) -> Path:
        conn = self.db.connect()
        cur = conn.execute("SELECT root_path FROM repositories WHERE id = ?", (rid,))
        r = cur.fetchone()
        if not r:
            raise RepositoryNotFoundError(rid)
        return Path(r["root_path"])
