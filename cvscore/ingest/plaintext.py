"""Plain text and Markdown extraction."""

from __future__ import annotations

import re
from pathlib import Path

from cvscore.ingest.document import ExtractionError, build_document
from cvscore.ingest.text_utils import is_section_keyword, join_lines, normalize_text, strip_bullet
from cvscore.models import BlockKind, DocType, SourceDocument, TextBlock

_MD_HEADING = re.compile(r"^#{1,6}\s+")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ExtractionError("Cannot decode text file as UTF-8 or Windows-1252.")


def extract_text_file(path: Path, *, doc_type: DocType, display_path: str) -> SourceDocument:
    blocks: list[TextBlock] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            blocks.append(TextBlock(kind=BlockKind.PARAGRAPH, text=join_lines(buf)))
            buf.clear()

    for raw_line in _read_text(path).splitlines():
        line = normalize_text(raw_line)
        if not line:
            flush()
            continue
        if _MD_HEADING.match(line) or is_section_keyword(line):
            flush()
            blocks.append(
                TextBlock(kind=BlockKind.HEADING, text=_MD_HEADING.sub("", line).rstrip(":"))
            )
            continue
        bullet, text = strip_bullet(line)
        if bullet:
            flush()
            blocks.append(TextBlock(kind=BlockKind.LIST_ITEM, text=text))
        else:
            buf.append(line)
    flush()
    return build_document(
        doc_type=doc_type,
        path=path,
        display_path=display_path,
        blocks=blocks,
        extractor="text",
    )
