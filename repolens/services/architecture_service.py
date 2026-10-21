"""Architecture summary service (Phase 2, P2-07).

The service loads persisted facts, detects explicit entry points from current
source (bounded), and delegates the deterministic graph/metric work to
``repolens.analysis.architecture``.
"""

from __future__ import annotations

from repolens.analysis.architecture import analyze_architecture
from repolens.analysis.entrypoints import detect_entry_points
from repolens.analysis.python_parser import parse_source
from repolens.config import Settings
from repolens.core.errors import AnalysisNotAvailableError, ParseError
from repolens.core.models import (
    ArchitectureSummary,
    EntryPoint,
    FindingSeverity,
    PageMeta,
    PaginatedFindings,
    ParseStatus,
)
from repolens.services.analysis_service import AnalysisService


class ArchitectureService:
    """Load facts and compute the architecture summary for a repository."""

    def __init__(self, analysis: AnalysisService, settings: Settings) -> None:
        self.analysis = analysis
        self.settings = settings

    def _entry_points(self, repository_id: str) -> tuple[list[EntryPoint], bool]:
        files = self.analysis.list_all_files(repository_id)
        scanned = files[: self.settings.architecture_entry_scan_limit]
        truncated = len(files) > len(scanned)
        out: list[EntryPoint] = []
        for file in scanned:
            if file.parse_status is not ParseStatus.PARSED:
                continue
            detail = self.analysis.get_file_detail(
                repository_id, file.rel_path, include_source=True
            )
            if not detail.source_available or detail.content is None:
                continue
            try:
                tree = parse_source(detail.content, file.rel_path)
            except ParseError:
                continue
            out.extend(detect_entry_points(tree, file.rel_path))
        out.sort(key=lambda entry: (entry.rel_path, entry.line))
        return out, truncated

    def summary(self, repository_id: str) -> ArchitectureSummary:
        """Compute the architecture summary of the latest completed run."""
        self.analysis.repo_svc.get_root(repository_id)
        if self.analysis.latest_run_id(repository_id) is None:
            raise AnalysisNotAvailableError(
                f"repository {repository_id} has no completed analysis run"
            )
        files = self.analysis.list_all_files(repository_id)
        symbols = self.analysis.list_all_symbols(repository_id)
        relationships = self.analysis.list_all_relationships(repository_id)
        metrics = self.analysis.list_all_function_metrics(repository_id)
        entry_points, entry_scan_truncated = self._entry_points(repository_id)
        summary = analyze_architecture(
            repository_id,
            files,
            symbols,
            relationships,
            metrics,
            entry_points,
            complexity_warning=self.settings.complexity_warning_threshold,
            fan_out_warning=self.settings.fan_out_warning_threshold,
            fan_in_warning=self.settings.fan_in_warning_threshold,
            max_cycles=self.settings.architecture_max_cycles,
            max_cycle_length=self.settings.architecture_max_cycle_length,
            top_components=self.settings.architecture_top_components,
            max_findings=self.settings.architecture_max_findings,
        )
        if entry_scan_truncated:
            summary.notes.append(
                "entry-point scan truncated at"
                f" {self.settings.architecture_entry_scan_limit} file(s)"
            )
            summary.truncated = True
        return summary

    def findings(
        self,
        repository_id: str,
        *,
        kind: str | None = None,
        severity: FindingSeverity | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedFindings:
        """Paginated findings from the architecture summary."""
        summary = self.summary(repository_id)
        findings = summary.findings
        if kind:
            findings = [finding for finding in findings if finding.kind == kind]
        if severity:
            findings = [finding for finding in findings if finding.severity is severity]
        total = len(findings)
        return PaginatedFindings(
            items=findings[offset : offset + limit],
            page=PageMeta(total=total, limit=limit, offset=offset),
        )
