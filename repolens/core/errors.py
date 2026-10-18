"""Domain error hierarchy for RepoLens.

Services raise these errors; the API layer maps them to HTTP status codes
with safe messages (no stack traces, no absolute host paths leaked).
"""

from __future__ import annotations


class RepoLensError(Exception):
    """Base class for all RepoLens domain errors."""

    code = "repolens_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RepositoryNotFoundError(RepoLensError):
    """A repository id does not exist."""

    code = "repository_not_found"


class RepositoryExistsError(RepoLensError):
    """A repository with the same resolved root path is already registered."""

    code = "repository_exists"


class InvalidRepositoryPathError(RepoLensError):
    """The supplied registration path is not an existing directory."""

    code = "invalid_repository_path"


class AnalysisRunNotFoundError(RepoLensError):
    """An analysis run id does not exist."""

    code = "analysis_run_not_found"


class AnalysisExecutionError(RepoLensError):
    """An analysis run failed unexpectedly; the run is recorded as failed."""

    code = "analysis_failed"


class FileNotFoundError_(RepoLensError):
    """A relative file path is not part of the latest completed analysis."""

    code = "file_not_found"


class SymbolNotFoundError(RepoLensError):
    """A symbol id does not exist."""

    code = "symbol_not_found"


class GraphNodeNotFoundError(RepoLensError):
    """A graph node id does not correspond to any known node."""

    code = "graph_node_not_found"


class PathTraversalError(RepoLensError):
    """A supplied relative path escapes the repository root or is malformed."""

    code = "path_traversal"


class GraphIntegrityError(RepoLensError):
    """Graph construction detected an inconsistency in extracted facts."""

    code = "graph_integrity"


class ParseError(Exception):
    """Internal signal: a single file failed to parse (not an API error).

    Carries the message and one-based line/offset reported by ``ast``.
    """

    def __init__(self, message: str, line: int | None, offset: int | None) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.offset = offset
