"""Reads every candidate's files into ``CandidateDocuments``."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from cvscore.ingest.discovery import (
    OCR,
    CandidateSource,
    discover_folder,
    discover_manifest,
    relative_display,
)
from cvscore.ingest.document import ExtractionError
from cvscore.ingest.forms import FORM_SUFFIXES, FormAnswer, extract_form, form_document
from cvscore.ingest.legacy import LEGACY_SUFFIXES, extract_legacy
from cvscore.ingest.ocr import IMAGE_SUFFIXES, image_document
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
    if suffix in IMAGE_SUFFIXES:
        return image_document(path, doc_type=doc_type, display_path=display_path)
    raise ExtractionError(f"Unsupported file type: {suffix or '(none)'}")


@dataclass
class IngestResult:
    candidates: list[CandidateDocuments]
    # candidate_id -> folder name / detected name. Usually identifies the person: keep it
    # out of anything sent to models or traces.
    identity: dict[str, str] = field(default_factory=dict)
    # candidate_id -> every name seen for the candidate, used to redact them.
    names: dict[str, list[str]] = field(default_factory=dict)
    sources: list[CandidateSource] = field(default_factory=list)


def _extract(path: Path, doc_type: DocType, display_path: str) -> SourceDocument:
    return extract_file(path, doc_type=doc_type, display_path=display_path)


def _read_manifest_files(source: CandidateSource, base: Path, ocr: OCR | None) -> None:
    for f in source.files:
        f.display_path = relative_display(f.path, base)
        try:
            f.document = extract_file(f.path, doc_type=f.doc_type, display_path=f.display_path)
        except ExtractionError as e:
            f.error = str(e)
        if f.document is not None and ocr is not None:
            f.document = ocr(f.path, f.document)


def ingest(
    root: Path,
    *,
    manifest: Path | None = None,
    form_table: dict[str, list[FormAnswer]] | None = None,
    form_table_path: Path | None = None,
    overrides: dict[str, DocType] | None = None,
    exclude: Iterable[str] = (),
    ocr: OCR | None = None,
) -> IngestResult:
    """Read and group every candidate's documents (see ``discovery`` for the layouts)."""
    if manifest:
        sources = discover_manifest(manifest)
        for source in sources:
            _read_manifest_files(source, manifest.parent, ocr)
    else:
        sources = discover_folder(root, _extract, overrides=overrides, exclude=exclude, ocr=ocr)
    result = IngestResult(candidates=[], sources=sources)
    for source in sources:
        cid = source.candidate_id
        if cid in result.identity:
            raise ValueError(f"Two candidates share the label '{source.label}'.")
        result.identity[cid] = source.label
        result.names[cid] = list(source.names)
        cand = CandidateDocuments(candidate_id=cid)
        for f in source.files:
            if f.document is not None:
                cand.documents.append(f.document)
            else:
                cand.errors.append(f"{f.display_path}: {f.error}")
        if form_table is not None and form_table_path is not None:
            keys = [source.label, source.folder, *source.names]
            answers = next((form_table[k] for k in keys if k and k in form_table), None)
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
