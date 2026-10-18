"""T5: bounded, non-executing source reading and encoding policy."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from repolens.ingestion.reader import read_source_file


def test_reads_utf8_and_hashes_bytes(tmp_path: Path):
    path = tmp_path / "a.py"
    data = "x = 'héllo'\n".encode()
    path.write_bytes(data)
    result = read_source_file(path, max_bytes=1000)
    assert result.ok
    assert result.encoding == "utf-8"
    assert result.content == "x = 'héllo'\n"
    assert result.content_hash == hashlib.sha256(data).hexdigest()
    assert result.size_bytes == len(data)


def test_reads_utf8_bom(tmp_path: Path):
    path = tmp_path / "bom.py"
    path.write_bytes(b"\xef\xbb\xbfx = 1\n")
    result = read_source_file(path, max_bytes=1000)
    assert result.ok
    assert result.encoding == "utf-8-sig"
    assert result.content == "x = 1\n"


def test_reads_utf16_le_bom_and_consumes_it(tmp_path: Path):
    """A UTF-16 LE file must decode *and* be parseable by the analyzer."""
    path = tmp_path / "le.py"
    data = "x = 1\n".encode("utf-16")  # adds the LE BOM
    path.write_bytes(data)
    result = read_source_file(path, max_bytes=1000)
    assert result.ok
    assert result.encoding == "utf-16"
    assert result.content == "x = 1\n"  # BOM stripped; the equality proves it
    # The analyzer must be able to parse what was read.
    ast.parse(result.content)


def test_reads_utf16_be_bom(tmp_path: Path):
    path = tmp_path / "be.py"
    path.write_bytes(b"\xfe\xff" + "def f():\n    return 1\n".encode("utf-16-be"))
    result = read_source_file(path, max_bytes=1000)
    assert result.ok
    assert result.content == "def f():\n    return 1\n"
    ast.parse(result.content)


def test_invalid_utf8_is_per_file_error(tmp_path: Path):
    path = tmp_path / "bad.py"
    path.write_bytes(b"x = '\xff\xfe'\n")
    result = read_source_file(path, max_bytes=1000)
    assert not result.ok
    assert result.content is None
    assert result.error is not None and "invalid_encoding" in result.error
    # The hash is still computed from the raw bytes.
    assert result.content_hash


def test_oversized_file_is_rejected_but_hashed(tmp_path: Path):
    path = tmp_path / "big.py"
    data = b"x" * 200
    path.write_bytes(data)
    result = read_source_file(path, max_bytes=100)
    assert not result.ok
    assert "file_too_large" in (result.error or "")
    assert result.content_hash == hashlib.sha256(data).hexdigest()


def test_missing_file_reports_read_error(tmp_path: Path):
    result = read_source_file(tmp_path / "nope.py", max_bytes=100)
    assert not result.ok
    assert result.error is not None and result.error.startswith("read_error")
    assert result.content_hash == ""
