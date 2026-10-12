"""Reads every candidate's files into ``CandidateDocuments``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cvscore.ingest.discovery import (
    CandidateSource,
    discover_folder,
    discover_manifest,
)
from cvscore.ingest.document import ExtractionError
from cvscore.ingest.forms import FORM_SUFFIXES, FormAnswer, extract_form, form_document
from cvscore.ingest.legacy import LEGACY_SUFFIXES, extract_legacy
from cvscore.ingest.pdf import extract_pdf
from cvscore.ingest.plaintext import extract_text_file
from cvscore.ingest.word import extract_docx
from cvscore.models import CandidateDocuments, DocType, SourceDocument


def extract_file(path: Path, *, doc_type: DocType, display_path: str) -> SourceDocument:
    """Extract one file, choosing the reader by extension."""
    suffix = path.suffix.lower()
    if not path.is_file():
        raise ExtractionError("File not found.")
    if suffix == ".pdf":
        return extract_pdf(path, doc_type=doc_type, display_path=display_path)
    if suffix == ".docx":
        return extract_docx(path, doc_type=doc_type, display_path=display_path)
    if suffix in (".txt", ".md"):
        return extract_text_file(path, doc_type=doc_type, display_path=display_path)
    if suffix in LEGACY_SUFFIXES:
        return extract_legacy(path, doc_type=doc_type, display_path=display_path)
    if suffix in FORM_SUFFIXES:
        return extract_form(path, display_path=display_path)
    raise ExtractionError(f"Unsupported file type: {suffix or '(none)'}")


@dataclass
class IngestResult:
    candidates: list[CandidateDocuments]
    # candidate_id -> folder name / manifest label. Usually identifies the person: keep it
    # out of anything sent to models or traces.
    identity: dict[str, str] = field(default_factory=dict)


def _display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def ingest(
    root: Path,
    *,
    manifest: Path | None = None,
    form_table: dict[str, list[FormAnswer]] | None = None,
    form_table_path: Path | None = None,
) -> IngestResult:
    sources: list[CandidateSource] = (
        discover_manifest(manifest) if manifest else discover_folder(root)
    )
    base = manifest.parent if manifest else root
    result = IngestResult(candidates=[])
    for source in sources:
        cid = source.candidate_id
        if cid in result.identity:
            raise ValueError(f"Two candidates share the label '{source.label}'.")
        result.identity[cid] = source.label
        cand = CandidateDocuments(candidate_id=cid)
        for f in source.files:
            display = _display(f.path, base)
            try:
                cand.documents.append(
                    extract_file(f.path, doc_type=f.doc_type, display_path=display)
                )
            except ExtractionError as e:
                cand.errors.append(f"{display}: {e}")
        if form_table is not None and form_table_path is not None:
            answers = form_table.get(source.label)
            if answers:
                cand.documents.append(
                    form_document(
                        answers,
                        path=form_table_path,
                        display_path=form_table_path.name,
                        extractor="form:table",
                    )
                )
        result.candidates.append(cand)
    return result
