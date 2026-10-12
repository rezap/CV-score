"""Turns extracted blocks into a ``SourceDocument``: sections, markdown text, quality score."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from cvscore.ingest.text_utils import is_section_keyword
from cvscore.models import BlockKind, DocType, SourceDocument, TextBlock
from cvscore.pii import has_contact_details

_TOKEN_RE = re.compile(r"\S+")
_WORDLIKE_RE = re.compile(r"[^\W\d_]{2,}")

MIN_CHARS = 200  # below this a CV almost certainly failed to extract (e.g. a scan)


class ExtractionError(Exception):
    """A file could not be read at all."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def render_markdown(blocks: list[TextBlock]) -> str:
    parts: list[str] = []
    prev: BlockKind | None = None
    for b in blocks:
        if b.kind is BlockKind.HEADING:
            line = f"## {b.text}"
        elif b.kind is BlockKind.LIST_ITEM:
            line = f"- {b.text}"
        elif b.kind is BlockKind.TABLE_ROW:
            line = f"| {b.text} |"
        else:
            line = b.text
        # Keep consecutive list items / table rows together; blank line between other blocks.
        tight = prev is b.kind and b.kind in (BlockKind.LIST_ITEM, BlockKind.TABLE_ROW)
        if parts and not tight:
            parts.append("")
        parts.append(line)
        prev = b.kind
    return "\n".join(parts)


def assign_sections(blocks: list[TextBlock]) -> None:
    """Set each block's ``section`` to the nearest preceding heading (in place)."""
    current: str | None = None
    for b in blocks:
        if b.kind is BlockKind.HEADING:
            current = b.text.rstrip(":").strip()
            b.section = current
        elif b.section is None:
            b.section = current


def _name_like(text: str) -> bool:
    """A short line without punctuation, like a name or a job title under it."""
    return len(text.split()) <= 4 and not re.search(r"[,:;.!?|]", text)


def mark_contact_area(blocks: list[TextBlock], *, top_of_cv: bool) -> None:
    """Flag identity and contact details for later redaction.

    Anywhere: short lines that contain an email, phone number or profile URL.
    CVs only: name-like lines before the first section heading ("Experience", "Profile", ...),
    which is where the candidate's name and headline sit.
    """
    for b in blocks:
        if (
            b.kind is not BlockKind.HEADING
            and len(b.text.split()) <= 8
            and has_contact_details(b.text)
        ):
            b.is_contact = True
    if not top_of_cv:
        return
    for b in blocks:
        if b.kind is BlockKind.HEADING and is_section_keyword(b.text):
            break
        if _name_like(b.text):
            b.is_contact = True


def quality_score(
    text: str, pages: int | None = None, *, check_length: bool = True
) -> tuple[float, list[str]]:
    """Heuristic 0-1 score for how cleanly text was extracted, plus warnings."""
    warnings: list[str] = []
    score = 1.0
    chars = len(text.strip())
    if check_length and chars < MIN_CHARS:
        warnings.append(
            f"Very little text extracted ({chars} characters); the file may be scanned "
            "or image-based."
        )
        score = min(score, 0.1 if chars < 20 else 0.4)
    cid = text.count("(cid:")
    if cid:
        warnings.append(f"{cid} unmapped glyphs '(cid:N)'; the PDF has broken font encoding.")
        score -= min(0.6, cid / 50)
    if "\ufffd" in text:
        warnings.append("Text contains replacement characters (U+FFFD).")
        score -= 0.2
    tokens = _TOKEN_RE.findall(text)
    if len(tokens) >= 30:
        wordlike = sum(1 for t in tokens if _WORDLIKE_RE.search(t)) / len(tokens)
        if wordlike < 0.6:
            warnings.append(f"Only {wordlike:.0%} of tokens look like words; text may be garbled.")
            score -= (0.6 - wordlike) * 1.5
    if pages and chars / pages < 100 and chars >= MIN_CHARS:
        warnings.append("Some pages have little or no text; parts may be scanned.")
        score -= 0.2
    return max(0.0, min(1.0, round(score, 2))), warnings


def build_document(
    *,
    doc_type: DocType,
    path: Path,
    display_path: str,
    blocks: list[TextBlock],
    extractor: str,
    warnings: list[str] | None = None,
    pages: int | None = None,
    detect_contact: bool = True,
    check_length: bool = True,
) -> SourceDocument:
    blocks = [b for b in blocks if b.text]
    assign_sections(blocks)
    if detect_contact:
        mark_contact_area(blocks, top_of_cv=doc_type is DocType.CV)
    text = render_markdown(blocks)
    quality, quality_warnings = quality_score(text, pages, check_length=check_length)
    return SourceDocument(
        doc_type=doc_type,
        path=display_path,
        sha256=sha256_file(path),
        text=text,
        blocks=blocks,
        extractor=extractor,
        quality=quality,
        warnings=[*(warnings or []), *quality_warnings],
    )
