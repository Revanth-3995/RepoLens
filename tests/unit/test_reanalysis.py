"""T16: re-analysis keeps only the latest details and never corrupts on failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from repolens.config import Settings
from repolens.persistence.database import Database
from repolens.services.analysis_service import AnalysisService
from repolens.services.repository_service import RepositoryService


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    root = tmp_path / "sample"
    root.mkdir()
    (root / "app.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    return root


@pytest.fixture
def service(tmp_path: Path, sample_repo: Path) -> tuple[AnalysisService, str]:
    settings = Settings(db_path=tmp_path / "re.db")
    database = Database(settings.db_path)
    database.connect()
    repo_id = RepositoryService(database, settings).register("sample", str(sample_repo)).id
    svc = AnalysisService(database, settings)
    try:
        yield svc, repo_id
    finally:
        database.close()


def test_reanalysis_replaces_details_and_keeps_history(service, sample_repo: Path):
    svc, repo_id = service
    first = svc.trigger(repo_id)
    assert len(svc.list_files(repo_id).items) == 1
    first_symbols = svc.list_symbols(repo_id).items
    assert any(s.name == "a" for s in first_symbols)

    # Add a file and re-analyze.
    (sample_repo / "extra.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    second = svc.trigger(repo_id)

    runs = svc.list_runs(repo_id)
    assert [r.id for r in runs][0] == second.id
    assert {first.id, second.id} <= {r.id for r in runs}

    files = svc.list_files(repo_id).items
    assert {f.rel_path for f in files} == {"app.py", "extra.py"}
    assert svc.get_run(first.id).status.value == "completed"
    assert svc.get_run(second.id).status.value == "completed"


def test_completed_metrics_survive_application_restart(tmp_path: Path, sample_repo: Path):
    settings = Settings(db_path=tmp_path / "restart.db")
    first_db = Database(settings.db_path)
    first_db.connect()
    repo_id = RepositoryService(first_db, settings).register("sample", str(sample_repo)).id
    first_service = AnalysisService(first_db, settings)
    try:
        first_service.trigger(repo_id)
        before, total = first_service.list_function_metrics(repo_id, limit=500)
        assert total == 1
        assert [(m.qualified_name, m.statement_count) for m in before] == [("app.a", 1)]
    finally:
        first_db.close()

    second_db = Database(settings.db_path)
    second_db.connect()
    try:
        second_service = AnalysisService(second_db, settings)
        after, total = second_service.list_function_metrics(repo_id, limit=500)
        assert total == 1
        assert [(m.qualified_name, m.statement_count) for m in after] == [("app.a", 1)]
    finally:
        second_db.close()


def test_no_duplicate_files_after_reanalysis(service):
    svc, repo_id = service
    for _ in range(3):
        svc.trigger(repo_id)
    files = svc.list_files(repo_id, limit=500).items
    assert [f.rel_path for f in files] == ["app.py"]
    rels = svc.list_relationships(repo_id, limit=500).items
    ids = [r.id for r in rels]
    assert len(ids) == len(set(ids))


def test_failed_run_preserves_previous_completed_data(service, monkeypatch):
    svc, repo_id = service
    good = svc.trigger(repo_id)
    files_before = svc.list_files(repo_id).items

    import repolens.services.analysis_service as module

    class Boom:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def analyze(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(module, "AnalysisPipeline", Boom)
    with pytest.raises(RuntimeError):
        svc.trigger(repo_id)

    runs = svc.list_runs(repo_id)
    statuses = {r.id: r.status.value for r in runs}
    assert statuses[good.id] == "completed"
    assert any(status == "failed" for status in statuses.values())

    # The completed run's details are untouched and still the queryable set.
    files_after = svc.list_files(repo_id).items
    assert [f.rel_path for f in files_after] == [f.rel_path for f in files_before]
