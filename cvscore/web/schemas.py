"""Response shapes shared by the web API and the background runs."""

from __future__ import annotations

from pydantic import BaseModel

from cvscore.ingest.discovery import CandidateSource
from cvscore.location import Location, locate
from cvscore.models import DocType


class FileSummary(BaseModel):
    path: str  # relative to the scanned folder
    doc_type: DocType
    reason: str
    names: list[str]
    quality: float | None
    warnings: list[str]
    error: str | None


class CandidateSummary(BaseModel):
    id: str
    label: str  # usually the person's name: shown only when the user asks for names
    folder: str | None
    grouped_by: list[str]
    warnings: list[str]
    files: list[FileSummary]
    location: Location = Location()  # where the documents say the person lives


def summarize(source: CandidateSource) -> CandidateSummary:
    return CandidateSummary(
        id=source.candidate_id,
        label=source.label,
        folder=source.folder,
        grouped_by=source.grouped_by,
        warnings=source.warnings,
        files=[
            FileSummary(
                path=f.display_path,
                doc_type=f.doc_type,
                reason=f.type_reason,
                names=[n for n, _ in f.names],
                quality=f.document.quality if f.document else None,
                warnings=[*f.warnings, *(f.document.warnings if f.document else [])],
                error=f.error,
            )
            for f in source.files
        ],
        location=locate([f.document for f in source.files if f.document], source.names),
    )
