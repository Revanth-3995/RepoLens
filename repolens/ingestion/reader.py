"""Bounded, non-executing source reader (decision AD-12).

Reads raw bytes, hashes them, and decodes with BOM sniffing (UTF-8/UTF-16)
followed by strict UTF-8. Invalid encodings and oversized files become
per-file errors — never fatal to a repository analysis.
"""

from __future__ import annotations

import codecs
import hashlib
from dataclasses import dataclass
from pathlib import Path

# NOTE: the UTF-16 codecs are ``utf-16`` (not ``utf-16-le``/``utf-16-be``):
# the endian-specific codecs do **not** strip the BOM, which would leave a
# leading U+FEFF and make ``ast.parse`` fail on every UTF-16 file. ``utf-16``
# consumes the BOM and selects the correct byte order from it.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


@dataclass(frozen=True)
class ReadResult:
    ok: bool
    content: str | None
    size_bytes: int
    content_hash: str  # sha256 of the raw bytes; "" when nothing was read
    encoding: str | None
    error: str | None


def read_source_file(path: Path, max_bytes: int) -> ReadResult:
    """Read and decode one source file without executing anything.

    The size check happens both on the discovered size and on the bytes
    actually read (defense in depth).
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        return ReadResult(
            ok=False,
            content=None,
            size_bytes=0,
            content_hash="",
            encoding=None,
            error=f"read_error: {exc.strerror or exc}",
        )

    size = len(data)
    content_hash = hashlib.sha256(data).hexdigest()
    if size > max_bytes:
        return ReadResult(
            ok=False,
            content=None,
            size_bytes=size,
            content_hash=content_hash,
            encoding=None,
            error=f"file_too_large: {size} > {max_bytes} bytes",
        )

    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                text = data.decode(encoding, errors="strict")
            except UnicodeDecodeError as exc:
                return ReadResult(
                    ok=False,
                    content=None,
                    size_bytes=size,
                    content_hash=content_hash,
                    encoding=None,
                    error=f"invalid_encoding: {exc.reason}",
                )
            return ReadResult(True, text, size, content_hash, encoding, None)

    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        return ReadResult(
            ok=False,
            content=None,
            size_bytes=size,
            content_hash=content_hash,
            encoding=None,
            error=f"invalid_encoding: {exc.reason} (byte position {exc.start})",
        )
    return ReadResult(True, text, size, content_hash, "utf-8", None)
