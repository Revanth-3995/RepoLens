"""Core domain models shared across the pipeline, persistence, and API.

Internal dataclasses describe in-flight analysis facts; pydantic models are
the serialization contract for persistence JSON columns and API responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ParseStatus(str, Enum):
    PARSED = "parsed"
    READ_ERROR = "read_error"
    PARSE_ERROR = "parse_error"


class SymbolKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class RelationshipType(str, Enum):
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS_FROM = "inherits_from"


class ResolutionStatus(str, Enum):
    RESOLVED_STATIC = "resolved_static"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    EXTERNAL = "external"
    DYNAMIC_OR_UNKNOWN = "dynamic_or_unknown"


class IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class GraphNodeType(str, Enum):
    REPOSITORY = "repository"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class CallForm(str, Enum):
    NAME = "name"
    ATTRIBUTE = "attribute"
    INDIRECT = "indirect"


class Direction(str, Enum):
    OUT = "out"
    IN = "in"
    BOTH = "both"


# ---------------------------------------------------------------------------
# In-flight analysis facts (dataclasses, no pydantic overhead in the pipeline)
# ---------------------------------------------------------------------------


@dataclass
class ParameterInfo:
    """One declared parameter of a function or method."""

    name: str
    kind: str  # posonly | arg | vararg | kwonly | kwarg
    annotation: str | None = None
    default: str | None = None


@dataclass
class SymbolDraft:
    """A symbol extracted from one file, before ids/persistence."""

    name: str
    qualified_name: str
    kind: SymbolKind
    ordinal: int
    start_line: int
    end_line: int
    parent_qualified: str | None = None
    docstring: str | None = None
    signature: str | None = None
    parameters: list[ParameterInfo] = field(default_factory=list)
    return_annotation: str | None = None
    bases: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    symbol_id: str = ""
    file_id: str = ""
    parent_id: str | None = None


@dataclass
class ImportRecord:
    """One import statement extracted from a file."""

    kind: str  # "import" | "from"
    module: str  # raw module text (may be "" for pure relative imports)
    level: int  # 0 for absolute, >=1 for relative
    alias: str | None = None  # "as" alias for plain imports
    names: list[tuple[str, str | None]] = field(default_factory=list)  # (name, alias)
    star: bool = False
    line: int = 0
    col: int = 0
    owner_qualified: str | None = None  # enclosing function/method, else module


@dataclass
class CallRecord:
    """One call expression extracted from a file."""

    func_repr: str  # source text of the callee, e.g. "self.start"
    form: CallForm
    root_name: str | None  # leftmost identifier for name/attribute calls
    attrs: tuple[str, ...]  # attribute chain after the root, e.g. ("start",)
    line: int = 0
    col: int = 0
    owner_qualified: str | None = None


@dataclass
class FileAnalysis:
    """Everything the pipeline learned about one discovered file."""

    rel_path: str
    module_name: str
    size_bytes: int
    content_hash: str
    parse_status: ParseStatus
    file_id: str = ""  # assigned by the resolver before relationship drafting
    error_message: str | None = None
    error_line: int | None = None
    error_offset: int | None = None
    symbols: list[SymbolDraft] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    star_import: bool = False
    # qualified name of an enclosing scope -> names bound locally there
    # (assignments, loop/with targets, exception names, nested-def names).
    # Used to detect shadowing that makes conservative resolution impossible.
    local_names: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class RelationshipDraft:
    """One extracted relationship with provenance and resolution status."""

    rel_type: RelationshipType
    source_file_id: str
    source_symbol_id: str | None
    source_rel_path: str
    source_line: int
    source_col: int
    resolution_status: ResolutionStatus
    target_ref: str  # human-meaningful reference: module, name, "builtin:len", expr
    target_symbol_id: str | None = None
    call_expr: str | None = None
    evidence: str = ""
    relationship_id: str = ""


@dataclass
class AnalysisIssueDraft:
    """A run-level error or warning produced during analysis."""

    severity: IssueSeverity
    category: str  # read_error | parse_error | discovery | limit_exceeded | persistence
    message: str
    rel_path: str | None = None
    line: int | None = None
    offset: int | None = None


@dataclass
class AnalysisOutcome:
    """The complete in-memory result of one pipeline run."""

    repository_id: str
    files: list[FileAnalysis] = field(default_factory=list)
    relationships: list[RelationshipDraft] = field(default_factory=list)
    issues: list[AnalysisIssueDraft] = field(default_factory=list)
    excluded_directories: int = 0
    excluded_files: int = 0
    discovered_count: int = 0
    truncated: bool = False
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# API / persistence serialization models (pydantic)
# ---------------------------------------------------------------------------


class ParameterModel(BaseModel):
    name: str
    kind: str
    annotation: str | None = None
    default: str | None = None


class AnalysisSummary(BaseModel):
    """Counts computed from actual analysis results (never fabricated)."""

    files_discovered: int = 0
    files_excluded: int = 0
    excluded_directories: int = 0
    files_read: int = 0
    files_parsed: int = 0
    files_read_errors: int = 0
    files_parse_errors: int = 0
    symbols_extracted: int = 0
    relationships_extracted: int = 0
    relationships_resolved: int = 0
    relationships_unresolved: int = 0
    relationships_external: int = 0
    relationships_ambiguous: int = 0
    relationships_dynamic: int = 0
    warnings: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    truncated: bool = False


class AnalysisRun(BaseModel):
    id: str
    repository_id: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    summary: AnalysisSummary | None = None
    error: str | None = None


class Repository(BaseModel):
    id: str
    name: str
    root_path: str
    registered_at: datetime
    latest_run: AnalysisRun | None = None


class FileRecord(BaseModel):
    id: str
    repository_id: str
    rel_path: str
    module_name: str
    file_type: str = "python"
    size_bytes: int
    content_hash: str
    parse_status: ParseStatus
    error_message: str | None = None
    error_line: int | None = None
    error_offset: int | None = None


class FileDetail(FileRecord):
    source_available: bool = False
    content_changed_since_analysis: bool = False
    content: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class SymbolRecord(BaseModel):
    id: str
    repository_id: str
    file_id: str
    rel_path: str
    name: str
    qualified_name: str
    kind: SymbolKind
    parent_id: str | None = None
    docstring: str | None = None
    signature: str | None = None
    parameters: list[ParameterModel] = Field(default_factory=list)
    return_annotation: str | None = None
    bases: list[str] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)
    start_line: int
    end_line: int
    ordinal: int = 0


class RelationshipRecord(BaseModel):
    id: str
    repository_id: str
    source_file_id: str
    source_symbol_id: str | None = None
    source_rel_path: str
    source_line: int
    source_col: int
    rel_type: RelationshipType
    target_symbol_id: str | None = None
    target_ref: str
    call_expr: str | None = None
    resolution_status: ResolutionStatus
    evidence: str


class AnalysisIssue(BaseModel):
    id: str
    run_id: str
    severity: IssueSeverity
    category: str
    message: str
    rel_path: str | None = None
    line: int | None = None
    offset: int | None = None


class GraphNode(BaseModel):
    id: str
    type: GraphNodeType
    label: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeSite(BaseModel):
    line: int | None = None
    evidence: str | None = None
    source_symbol_id: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    sites: list[GraphEdgeSite] = Field(default_factory=list)


class NeighborResult(BaseModel):
    node: GraphNode
    neighbors: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class PathResult(BaseModel):
    found: bool
    source: str
    target: str
    nodes: list[str] = Field(default_factory=list)
    length: int = 0
    reason: str | None = None


class GraphStats(BaseModel):
    repository_id: str
    nodes: int = 0
    edges: int = 0
    by_node_type: dict[str, int] = Field(default_factory=dict)
    by_edge_type: dict[str, int] = Field(default_factory=dict)


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class PaginatedFiles(BaseModel):
    items: list[FileRecord]
    page: PageMeta


class PaginatedSymbols(BaseModel):
    items: list[SymbolRecord]
    page: PageMeta


class PaginatedRelationships(BaseModel):
    items: list[RelationshipRecord]
    page: PageMeta


class PaginatedIssues(BaseModel):
    items: list[AnalysisIssue]
    page: PageMeta


class PaginatedNodes(BaseModel):
    items: list[GraphNode]
    page: PageMeta


class PaginatedEdges(BaseModel):
    items: list[GraphEdge]
    page: PageMeta


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
