"""T15: SQLite schema, constraints, foreign keys and indexes."""

from __future__ import annotations

import sqlite3

import pytest

from repolens.persistence.database import Database


def _tables(db: Database) -> set[str]:
    conn = db.connect()
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows}


def test_expected_tables_exist(db: Database):
    assert {
        "schema_meta",
        "repositories",
        "analysis_runs",
        "files",
        "symbols",
        "relationships",
        "analysis_errors",
    } <= _tables(db)


def test_expected_indexes_exist(db: Database):
    conn = db.connect()
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    names = {r["name"] for r in rows}
    assert "idx_files_repo_run" in names
    assert "idx_symbols_run_name" in names
    assert "idx_rels_run_type" in names


def test_foreign_keys_enabled(db: Database):
    conn = db.connect()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_wal_journal_mode(db: Database):
    conn = db.connect()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_unique_file_per_run(db: Database):
    conn = db.connect()
    conn.execute(
        "INSERT INTO repositories (id, name, root_path, registered_at) VALUES (?,?,?,?)",
        ("r", "n", "/x", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO analysis_runs (id, repository_id, status, started_at) VALUES (?,?,?,?)",
        ("run1", "r", "completed", "2026-01-01T00:00:00"),
    )
    row = ("f1", "r", "run1", "a.py", "a", "python", 3, "h", "parsed")
    conn.execute(
        "INSERT INTO files (id, repository_id, run_id, rel_path, module_name, file_type,"
        " size_bytes, content_hash, parse_status) VALUES (?,?,?,?,?,?,?,?,?)",
        row,
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO files (id, repository_id, run_id, rel_path, module_name, file_type,"
            " size_bytes, content_hash, parse_status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("f2", "r", "run1", "a.py", "a", "python", 3, "h", "parsed"),
        )
    conn.rollback()


def test_foreign_key_violation_rejected(db: Database):
    conn = db.connect()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO files (id, repository_id, run_id, rel_path, module_name, file_type,"
            " size_bytes, content_hash, parse_status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("f1", "missing-repo", "missing-run", "a.py", "a", "python", 3, "h", "parsed"),
        )
    conn.rollback()


def test_parse_status_check_constraint(db: Database):
    conn = db.connect()
    conn.execute(
        "INSERT INTO repositories (id, name, root_path, registered_at) VALUES (?,?,?,?)",
        ("r", "n", "/x", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO analysis_runs (id, repository_id, status, started_at) VALUES (?,?,?,?)",
        ("run1", "r", "completed", "2026-01-01T00:00:00"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO files (id, repository_id, run_id, rel_path, module_name, file_type,"
            " size_bytes, content_hash, parse_status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("f1", "r", "run1", "a.py", "a", "python", 3, "h", "bogus"),
        )
    conn.rollback()
