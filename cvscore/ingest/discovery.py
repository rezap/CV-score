"""Finds each candidate's files and decides what kind of document each one is.

Three layouts are supported:
- **Folder per candidate** (default): ``candidates/<anything>/*`` (searched recursively).
- **Manifest**: a CSV with a ``candidate`` column and ``cv``, ``cover_letter``, ``form``
  path columns (relative to the manifest's folder).
- **Flat folder**: files directly in ``candidates/`` grouped by name, e.g.
  ``jane_doe_cv.pdf`` + ``jane_doe_cover_letter.docx`` → candidate ``jane_doe``.

The folder name / manifest label usually identifies the person, so it is replaced by a
pseudonymous ``candidate_id``; the mapping is returned separately.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from cvscore.ingest.forms import FORM_SUFFIXES
from cvscore.ingest.legacy import LEGACY_SUFFIXES
from cvscore.models import DocType

DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"}) | LEGACY_SUFFIXES
SUPPORTED_SUFFIXES = DOCUMENT_SUFFIXES | FORM_SUFFIXES

# Filename tokens that identify the document type, checked in this order.
_TYPE_TOKENS: list[tuple[DocType, frozenset[str]]] = [
    (
        DocType.COVER_LETTER,
        frozenset(
            {
                "cover",
                "letter",
                "coverletter",
                "motivation",
                "motivational",
                "anschreiben",
                "motivationsschreiben",
                "lettre",
            }
        ),
    ),
    (
        DocType.CV,
        frozenset({"cv", "resume", "résumé", "lebenslauf", "curriculum", "vitae", "vita"}),
    ),
    (
        DocType.APPLICATION_FORM,
        frozenset(
            {"form", "application", "questionnaire", "answers", "bewerbungsformular", "fragebogen"}
        ),
    ),
]
_ALL_TYPE_TOKENS = frozenset().union(*(t for _, t in _TYPE_TOKENS))
_TOKEN_SPLIT = re.compile(r"[\s_\-.()]+")


@dataclass
class CandidateFile:
    path: Path
    doc_type: DocType


@dataclass
class CandidateSource:
    label: str  # folder name / manifest label; may identify the person
    files: list[CandidateFile] = field(default_factory=list)

    @property
    def candidate_id(self) -> str:
        return pseudonymous_id(self.label)


def pseudonymous_id(label: str) -> str:
    return "c-" + hashlib.sha256(label.strip().lower().encode()).hexdigest()[:10]


def _tokens(stem: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(stem.lower()) if t]


def classify(path: Path) -> DocType | None:
    """Document type from the file name, or None if the name doesn't say."""
    tokens = set(_tokens(path.stem))
    for doc_type, words in _TYPE_TOKENS:
        if tokens & words:
            return doc_type
    if path.suffix.lower() in FORM_SUFFIXES:
        return DocType.APPLICATION_FORM
    return None


def _is_candidate_file(path: Path) -> bool:
    name = path.name
    return (
        path.is_file()
        and path.suffix.lower() in SUPPORTED_SUFFIXES
        and not name.startswith((".", "~$"))  # hidden files, Word lock files
    )


def _typed_files(paths: list[Path]) -> list[CandidateFile]:
    """Classify files; if exactly one document has no recognisable name, it is the CV."""
    typed = [(p, classify(p)) for p in sorted(paths)]
    unknown_docs = [p for p, t in typed if t is None and p.suffix.lower() in DOCUMENT_SUFFIXES]
    has_cv = any(t is DocType.CV for _, t in typed)
    files = []
    for p, t in typed:
        if t is None:
            t = DocType.CV if (len(unknown_docs) == 1 and not has_cv) else DocType.OTHER
        files.append(CandidateFile(p, t))
    return files


def _flat_key(path: Path) -> str:
    tokens = _tokens(path.stem)
    while len(tokens) > 1 and tokens[-1] in _ALL_TYPE_TOKENS:
        tokens.pop()
    return "_".join(tokens)


def discover_folder(root: Path) -> list[CandidateSource]:
    subdirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    sources: list[CandidateSource] = []
    for d in subdirs:
        files = [p for p in d.rglob("*") if _is_candidate_file(p)]
        if files:
            sources.append(CandidateSource(label=d.name, files=_typed_files(files)))
    loose = [p for p in root.iterdir() if _is_candidate_file(p)]
    groups: dict[str, list[Path]] = {}
    for p in loose:
        groups.setdefault(_flat_key(p), []).append(p)
    for key in sorted(groups):
        sources.append(CandidateSource(label=key, files=_typed_files(groups[key])))
    return sources


_MANIFEST_COLUMNS: dict[str, DocType] = {
    "cv": DocType.CV,
    "cv_path": DocType.CV,
    "resume": DocType.CV,
    "cover_letter": DocType.COVER_LETTER,
    "cover_letter_path": DocType.COVER_LETTER,
    "form": DocType.APPLICATION_FORM,
    "form_path": DocType.APPLICATION_FORM,
    "application_form": DocType.APPLICATION_FORM,
}


def discover_manifest(manifest: Path) -> list[CandidateSource]:
    base = manifest.parent
    with manifest.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    columns = {c.strip().lower(): c for c in rows[0] if c}
    label_col = columns.get("candidate") or columns.get("candidate_id")
    if label_col is None:
        raise ValueError("Manifest needs a 'candidate' column.")
    sources = []
    for row in rows:
        label = (row.get(label_col) or "").strip()
        if not label:
            continue
        files = []
        for col_lower, col in columns.items():
            doc_type = _MANIFEST_COLUMNS.get(col_lower)
            value = (row.get(col) or "").strip()
            if doc_type and value:
                files.append(CandidateFile(base / value, doc_type))
        sources.append(CandidateSource(label=label, files=files))
    return sources
