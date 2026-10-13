"""Finds each candidate's files, decides what each document is, and whose it is.

Point it at any folder; the layout is worked out from the files:

- **Folder per candidate**: ``candidates/Jane Doe/{cv.pdf, letter.docx}``. A folder whose
  documents all belong to one person (at most one CV, or CVs with the same name/email)
  is one candidate, at any depth.
- **Flat folder**: ``candidates/*.pdf`` with everyone's files together, or CVs and cover
  letters in separate sub-folders. Files are matched to candidates by the name in the
  document (top of the CV, letter signature), the email address, and the file name
  (``Jane_Doe_CV.pdf`` + ``Cover letter - Jane Doe.docx``).
- **Manifest**: a CSV with a ``candidate`` column and ``cv``, ``cover_letter``, ``form``
  path columns (relative to the manifest's folder); nothing is guessed.

Document types come from the file name and the content (see ``identify``). The folder
name or detected name usually identifies the person, so each candidate gets a
pseudonymous ``candidate_id``; the mapping is returned separately.
"""

from __future__ import annotations

import csv
import hashlib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from cvscore.ingest.document import MIN_CHARS, ExtractionError, mark_contact_area
from cvscore.ingest.forms import FORM_SUFFIXES
from cvscore.ingest.identify import (
    doc_type_of,
    emails_in,
    file_tokens,
    filename_key,
    looks_like_name,
    names_in,
    pretty_name,
    same_person,
    type_from_filename,
)
from cvscore.ingest.legacy import LEGACY_SUFFIXES
from cvscore.models import DocType, SourceDocument

DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"}) | LEGACY_SUFFIXES
SUPPORTED_SUFFIXES = DOCUMENT_SUFFIXES | FORM_SUFFIXES

Extractor = Callable[[Path, DocType, str], SourceDocument]


@dataclass
class CandidateFile:
    path: Path
    doc_type: DocType
    display_path: str = ""
    document: SourceDocument | None = None
    error: str | None = None
    type_reason: str = ""
    names: list[tuple[str, str]] = field(default_factory=list)  # (name, where found)
    emails: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


@dataclass
class CandidateSource:
    label: str  # folder name, detected name or file name; may identify the person
    files: list[CandidateFile] = field(default_factory=list)
    names: list[str] = field(default_factory=list)  # every name seen, for redaction
    grouped_by: list[str] = field(default_factory=list)  # why these files belong together
    warnings: list[str] = field(default_factory=list)
    folder: str | None = None  # the candidate's own folder, if any

    @property
    def candidate_id(self) -> str:
        return pseudonymous_id(self.label)


def pseudonymous_id(label: str) -> str:
    return "c-" + hashlib.sha256(label.strip().lower().encode()).hexdigest()[:10]


def classify(path: Path) -> DocType | None:
    """Document type from the file name alone, or None if the name doesn't say."""
    by_name = type_from_filename(path)
    if by_name is None and path.suffix.lower() in FORM_SUFFIXES:
        return DocType.APPLICATION_FORM
    return by_name


def is_candidate_file(path: Path) -> bool:
    name = path.name
    return (
        path.is_file()
        and path.suffix.lower() in SUPPORTED_SUFFIXES
        and not name.startswith((".", "~$"))  # hidden files, Word lock files
    )


def relative_display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def set_doc_type(doc: SourceDocument, doc_type: DocType) -> None:
    """Retype an extracted document; a CV also gets its top area flagged for redaction."""
    doc.doc_type = doc_type
    if doc_type is DocType.CV:
        mark_contact_area(doc.blocks, top_of_cv=True)


def read_file(
    path: Path, root: Path, extract: Extractor, override: DocType | None = None
) -> CandidateFile:
    """Extract one file and work out its type, names and emails."""
    display = relative_display(path, root)
    f = CandidateFile(path=path, doc_type=DocType.OTHER, display_path=display)
    try:
        f.document = extract(path, DocType.OTHER, display)
    except ExtractionError as e:
        f.error = str(e)
    if override is not None:
        f.doc_type, f.type_reason = override, "set by user"
    else:
        guess = doc_type_of(path, f.document)
        f.doc_type, f.type_reason, f.warnings = guess.doc_type, guess.reason, guess.warnings
    if f.document is not None and f.doc_type is not DocType.APPLICATION_FORM:
        set_doc_type(f.document, f.doc_type)
    f.names = names_in(f.document, path)
    f.emails = emails_in(f.document)
    return f


# --- Grouping ------------------------------------------------------------------------


@dataclass
class _Group:
    files: list[CandidateFile]
    folder: Path | None = None  # set when the group is a whole single-person folder
    reasons: set[str] = field(default_factory=set)

    def cvs(self) -> list[CandidateFile]:
        return [f for f in self.files if f.doc_type is DocType.CV]

    def names(self, *, cv_only: bool = False) -> list[str]:
        files = self.cvs() if cv_only else self.files
        return [n for f in files for n, _ in f.names]

    def emails(self, *, cv_only: bool = False) -> set[str]:
        files = self.cvs() if cv_only else self.files
        return {e for f in files for e in f.emails}


def _one_person(files: list[CandidateFile]) -> bool:
    """True if these files can't belong to two people: at most one CV, or all CVs agree."""
    cvs = [f for f in files if f.doc_type is DocType.CV]
    if len(cvs) <= 1:
        names = [n for f in files for n, where in f.names if where != "file name"]
        return all(same_person(a, b) for a in names for b in names)
    first = cvs[0]
    for other in cvs[1:]:
        shared_email = bool(first.emails & other.emails)
        names_a = [n for n, _ in first.names]
        names_b = [n for n, _ in other.names]
        same_name = any(same_person(a, b) for a in names_a for b in names_b)
        if not (shared_email or same_name):
            return False
    return True


def _conflict(a: _Group, b: _Group) -> bool:
    """Two groups that each have a CV are only merged if the CVs agree."""
    if not (a.cvs() and b.cvs()):
        return False
    ea, eb = a.emails(cv_only=True), b.emails(cv_only=True)
    if ea and eb:
        return not (ea & eb)
    na, nb = a.names(cv_only=True), b.names(cv_only=True)
    if na and nb:
        return not any(same_person(x, y) for x in na for y in nb)
    return False


def _folder_name(g: _Group) -> str | None:
    """The group's folder name if it looks like a person's name ("Jane Doe/", "jane_doe/")."""
    if g.folder is None:
        return None
    name = pretty_name(g.folder.name.replace("_", " "))
    return name if looks_like_name(name) else None


def _different_named_folders(a: _Group, b: _Group) -> bool:
    """Folders named after two different people are kept apart, whatever is inside."""
    na, nb = _folder_name(a), _folder_name(b)
    return na is not None and nb is not None and not same_person(na, nb)


def _units(folder: Path, files: dict[Path, CandidateFile], root: Path) -> list[_Group]:
    """Split a folder into starting groups: whole single-person folders, else single files."""
    inside = [f for p, f in files.items() if folder in p.parents]
    if not inside:
        return []
    if folder != root and _one_person(inside):
        return [_Group(files=inside, folder=folder, reasons={"same folder"})]
    groups = [_Group(files=[f]) for p, f in files.items() if p.parent == folder]
    for sub in sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")):
        groups.extend(_units(sub, files, root))
    return groups


def _merge_all(groups: list[_Group]) -> list[_Group]:
    """Merge groups that share an email, a name, or a file name (minus type words)."""

    def keys(g: _Group) -> Iterable[tuple[str, str]]:
        for e in g.emails():
            yield "email", e
        for f in g.files:
            # "<who>_cv.pdf", "<who> - cover letter.docx": the file name minus the words
            # that describe the document. A bare "<who>.pdf" is not used.
            key = filename_key(f.path)
            if len(key) >= 2 and key != " ".join(file_tokens(f.path.stem)):
                yield "file name", key

    merged = True
    while merged:
        merged = False
        for i, a in enumerate(groups):
            for j in range(i + 1, len(groups)):
                b = groups[j]
                if _conflict(a, b) or _different_named_folders(a, b):
                    continue
                shared = {k for k in keys(a)} & {k for k in keys(b)}
                why = {kind for kind, _ in shared}
                if not why and any(same_person(x, y) for x in a.names() for y in b.names()):
                    why = {"name"}
                if why:
                    a.files.extend(b.files)
                    a.reasons |= why
                    a.folder = a.folder or b.folder
                    del groups[j]
                    merged = True
                    break
            if merged:
                break
    return groups


def _label(g: _Group) -> str:
    if _folder_name(g) is not None:
        assert g.folder is not None
        return g.folder.name  # "Jane Doe/": the folder name was chosen by a person
    weights: dict[str, float] = {}
    for f in g.files:
        for rank, (name, where) in enumerate(f.names):
            score = 3 if f.doc_type is DocType.CV else 2
            bonus = score - (1 if where == "file name" else 0) - rank * 0.1
            weights[name] = weights.get(name, 0.0) + bonus
    if weights:
        return max(weights, key=lambda n: weights[n])
    keys = [filename_key(f.path) for f in g.files if filename_key(f.path)]
    if g.folder is not None:
        return g.folder.name
    if keys:
        return " ".join(t.capitalize() for t in Counter(keys).most_common(1)[0][0].split())
    return g.files[0].path.stem


def _finish(g: _Group) -> CandidateSource:
    files: list[CandidateFile] = []
    duplicates: list[str] = []
    seen: dict[str, str] = {}
    for f in sorted(g.files, key=lambda f: f.display_path):
        sha = f.document.sha256 if f.document is not None else None
        if sha is not None and sha in seen:
            duplicates.append(f"{f.display_path} is a copy of {seen[sha]}; skipped.")
            continue
        if sha is not None:
            seen[sha] = f.display_path
        files.append(f)
    # No CV, and one document with too little text to tell (e.g. a scan): that is the CV.
    if not any(f.doc_type is DocType.CV for f in files):
        unknown = [
            f
            for f in files
            if f.doc_type is DocType.OTHER
            and f.document is not None
            and len(f.document.text.strip()) < MIN_CHARS
            and f.type_reason != "file name"  # e.g. "notes.pdf"
        ]
        if len(unknown) == 1 and unknown[0].type_reason != "set by user":
            unknown[0].doc_type = DocType.CV
            unknown[0].type_reason = "only document"
            assert unknown[0].document is not None
            set_doc_type(unknown[0].document, DocType.CV)
    label = _label(g)
    names: list[str] = []
    for n in [label, *g.names()]:
        if (looks_like_name(n) or n == label) and n not in names:
            names.append(n)
    src = CandidateSource(
        label=label,
        files=files,
        names=names,
        grouped_by=sorted(g.reasons),
        folder=g.folder.name if g.folder is not None else None,
    )
    src.warnings.extend(duplicates)
    kinds = Counter(f.doc_type for f in files)
    if not kinds[DocType.CV]:
        src.warnings.append("No CV found for this candidate.")
    if kinds[DocType.CV] > 1:
        src.warnings.append(f"{kinds[DocType.CV]} CVs; all are assessed together.")
    return src


def _unique_labels(sources: list[CandidateSource]) -> None:
    seen: Counter[str] = Counter()
    for s in sources:
        key = s.label.strip().lower()
        seen[key] += 1
        if seen[key] > 1:
            s.label = f"{s.label} ({seen[key]})"


def discover_folder(
    root: Path,
    extract: Extractor | None = None,
    *,
    overrides: dict[str, DocType] | None = None,
    exclude: Iterable[str] = (),
) -> list[CandidateSource]:
    """Read every supported file under ``root`` and group the files into candidates.

    ``overrides`` maps display paths (relative to ``root``) to a document type set by a
    person; ``exclude`` lists display paths to skip.
    """
    if extract is None:
        from cvscore.ingest.pipeline import extract_file  # here: pipeline imports this module

        def extract(path: Path, doc_type: DocType, display: str) -> SourceDocument:
            return extract_file(path, doc_type=doc_type, display_path=display)

    overrides = overrides or {}
    skip = set(exclude)
    files: dict[Path, CandidateFile] = {}
    for path in sorted(p for p in root.rglob("*") if is_candidate_file(p)):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        display = relative_display(path, root)
        if display in skip:
            continue
        files[path] = read_file(path, root, extract, overrides.get(display))
    groups = _merge_all(_units(root, files, root))
    sources = sorted((_finish(g) for g in groups), key=lambda s: s.label.lower())
    _unique_labels(sources)
    return sources


# --- Manifest ------------------------------------------------------------------------

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
                files.append(CandidateFile(base / value, doc_type, type_reason="manifest"))
        sources.append(CandidateSource(label=label, files=files, names=[label]))
    return sources
