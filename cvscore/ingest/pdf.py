"""PDF text extraction with pdfplumber, aware of multi-column CV layouts.

Per page:
1. Words (with font size) are grouped into lines.
2. A vertical gutter that almost no word crosses marks a two-column layout. If both sides
   have their own section headings (a sidebar CV), the left column is read before the
   right one, in bands separated by lines that cross the gutter (e.g. a name banner).
   Otherwise the left side is a date or label column, read row by row.
3. Lines are merged into paragraphs and list items; headings are recognised by larger type
   or by matching a known section name.
Repeated header/footer lines (same text on most pages) are dropped.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

from cvscore.ingest.document import ExtractionError, build_document
from cvscore.ingest.text_utils import (
    is_section_keyword,
    join_lines,
    normalize_text,
    strip_bullet,
    tidy_heading,
)
from cvscore.models import BlockKind, DocType, SourceDocument, TextBlock

_GLYPH_BULLET_RE = re.compile(r"^\(cid:\d+\)\s*")  # bullet glyph without a unicode mapping
# Icon fonts (envelope, phone, GitHub logo, ...): their glyphs decode to "(cid:N)" or to
# stray letters, so their characters are dropped. Symbol and Wingdings are kept: Word draws
# list bullets with them.
_ICON_FONT_RE = re.compile(
    r"awesome|simpleicons|academicons|marvosym|fontello|materialicons|material-?symbols|"
    r"ionicons|icomoon|bootstrap-?icons|tabler|feather|phosphor|remixicon|lineicons|"
    r"glyphicons|octicons|devicon|typicons|entypo|socicon",
    re.I,
)
LINE_TOLERANCE = 3.0  # points; words whose tops differ less than this share a line
# Gap between letters, as a share of the font size, above which they are separate words.
# Letters of a word sit at most ~0.04 em apart and word gaps start around 0.1 em; a fixed
# tolerance in points merges words in small or tightly justified text (LaTeX PDFs have
# no space characters, only gaps).
WORD_GAP_RATIO = 0.08
# A page with less text than this and a large image is taken for a scan (sent to OCR).
SCAN_PAGE_MAX_CHARS = 100
GUTTER_MIN_WIDTH = 10.0  # points
MARGIN_FRACTION = 0.08  # top/bottom share of the page searched for running headers/footers


@dataclass
class _Line:
    top: float
    bottom: float
    words: list[dict[str, Any]] = field(default_factory=list)

    @property
    def x0(self) -> float:
        return float(min(w["x0"] for w in self.words))

    @property
    def x1(self) -> float:
        return float(max(w["x1"] for w in self.words))

    @property
    def text(self) -> str:
        return normalize_text(" ".join(w["text"] for w in self.words))

    @property
    def size(self) -> float:
        return float(statistics.median(w["size"] for w in self.words))


def _join_letter_spaced(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join runs of 3+ single letters ("E X P E R I E N C E", tracked headings) into a word."""
    out: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    def flush() -> None:
        if len(run) >= 3:
            # A clearly wider gap between letters is a word space: "A B O U T   M E".
            gaps = [b["x0"] - a["x1"] for a, b in zip(run, run[1:], strict=False)]
            letter_gap = min(gaps)
            text = run[0]["text"]
            for gap, w in zip(gaps, run[1:], strict=True):
                text += (" " if gap > letter_gap * 1.6 + 0.1 * w["size"] else "") + w["text"]
            out.append({**run[0], "text": text, "x1": run[-1]["x1"]})
        else:
            out.extend(run)
        run.clear()

    for w in words:
        single = len(w["text"]) == 1 and w["text"].isalpha()
        close = run and w["x0"] - run[-1]["x1"] < 1.5 * w["size"]
        if single and (not run or close):
            run.append(w)
            continue
        flush()
        if single:
            run.append(w)
        else:
            out.append(w)
    flush()
    return out


def _group_lines(words: list[dict[str, Any]]) -> list[_Line]:
    lines: list[_Line] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        for line in lines:
            if abs(line.top - w["top"]) <= LINE_TOLERANCE:
                line.words.append(w)
                line.bottom = max(line.bottom, w["bottom"])
                break
        else:
            lines.append(_Line(top=w["top"], bottom=w["bottom"], words=[w]))
    for line in lines:
        line.words.sort(key=lambda w: w["x0"])
        line.words = _join_letter_spaced(line.words)
    lines.sort(key=lambda ln: ln.top)
    return lines


def _split_line(line: _Line, gx0: float, gx1: float) -> tuple[_Line | None, _Line | None, bool]:
    """Split a line at the gutter; third value is True if a word straddles it."""
    left = [w for w in line.words if w["x1"] <= gx0 + 1]
    right = [w for w in line.words if w["x0"] >= gx1 - 1]
    spans = len(left) + len(right) < len(line.words)

    def part(ws: list[dict[str, Any]]) -> _Line | None:
        return _Line(line.top, line.bottom, ws) if ws else None

    return part(left), part(right), spans


def _find_gutter(lines: list[_Line], width: float) -> tuple[float, float] | None:
    """Widest vertical band in the middle of the page that (almost) no word covers and
    that has text on both sides (an empty right margin is not a gutter)."""
    if len(lines) < 3:
        return None
    lo, hi = int(width * 0.15), int(width * 0.85)
    cover = [0] * (int(width) + 2)
    for line in lines:
        for w in line.words:
            for x in range(max(lo, int(w["x0"])), min(hi, int(w["x1"]) + 1)):
                cover[x] += 1
    allowed = max(1, len(lines) // 10)  # a few full-width lines (name banner) may cross
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for x in range(lo, hi + 1):
        free = cover[x] <= allowed and x < hi
        if free and start is None:
            start = x
        elif not free and start is not None:
            if x - start >= GUTTER_MIN_WIDTH:
                bands.append((start, x))
            start = None
    for band in sorted(bands, key=lambda b: b[1] - b[0], reverse=True):
        gutter = _narrow(band, cover)
        gx0, gx1 = gutter
        # Both sides must hold text, otherwise it is an indent or a ragged right edge.
        left = sum(1 for ln in lines if any(w["x1"] <= gx0 + 1 for w in ln.words))
        right = sum(1 for ln in lines if any(w["x0"] >= gx1 - 1 for w in ln.words))
        if min(left, right) >= 2:
            return gutter
    return None


def _narrow(band: tuple[int, int], cover: list[int]) -> tuple[float, float]:
    """A few long lines of the left column may reach into the band: split at its least
    covered strip, so only lines that really cross both columns count as crossing."""
    least = min(cover[band[0] : band[1]])
    strips: list[tuple[int, int]] = []
    for x in range(band[0], band[1]):
        if cover[x] == least:
            if strips and strips[-1][1] == x:
                strips[-1] = (strips[-1][0], x + 1)
            else:
                strips.append((x, x + 1))
    narrow = max(strips, key=lambda s: s[1] - s[0])
    return float(narrow[0]), float(narrow[1])


Run = tuple[list[_Line], float]  # lines in reading order + the right edge of their column


def _reading_order(lines: list[_Line], width: float, right_edge: float) -> list[Run]:
    """Return runs of lines (columns / bands) in reading order, each with its right edge.

    With a gutter there are two layouts to tell apart:
    - Sidebar CVs: each side has its own section headings ("Skills" left, "Experience"
      right). Read the left column, then the right one, band by band.
    - Label/date columns: dates or section labels on the left belong to the entry on the
      right of the same row. Read row by row, left part first.
    """
    gutter = _find_gutter(lines, width)
    if gutter is None:
        return [(lines, right_edge)]
    gx0, gx1 = gutter
    parts = [_split_line(line, gx0, gx1) for line in lines]
    left_headings = any(lp and is_section_keyword(lp.text) for lp, _, sp in parts if not sp)
    right_headings = any(rp and is_section_keyword(rp.text) for _, rp, sp in parts if not sp)

    if not (left_headings and right_headings):
        row_run: list[_Line] = []
        for line, (lpart, rpart, spans) in zip(lines, parts, strict=True):
            if spans:
                row_run.append(line)
            else:
                row_run.extend(p for p in (lpart, rpart) if p)
        return [(row_run, right_edge)]

    # The left column's own right edge (where its lines wrap), not the middle of the gutter.
    left_ends = sorted(lp.x1 for lp, _, sp in parts if lp and not sp)
    sidebar_edge = left_ends[int(len(left_ends) * 0.9)] if left_ends else (gx0 + gx1) / 2
    runs: list[Run] = []
    left: list[_Line] = []
    right: list[_Line] = []

    def flush() -> None:
        if left:
            runs.append((left.copy(), sidebar_edge))
        if right:
            runs.append((right.copy(), right_edge))
        left.clear()
        right.clear()

    for line, (lpart, rpart, spans) in zip(lines, parts, strict=True):
        if spans:
            flush()
            runs.append(([line], right_edge))
            continue
        if lpart:
            left.append(lpart)
        if rpart:
            right.append(rpart)
    flush()
    return runs


def _deliberate_break(prev: _Line, line: _Line, right_edge: float) -> bool:
    """True if ``prev`` ended early on purpose rather than wrapping into ``line``."""
    prev_text, text = prev.text, line.text
    if re.search(r"[^\W\d_]-$", prev_text) or text[:1].islower():
        return False  # a word broken across lines, or a sentence carrying on
    first = line.words[0]
    first_width = float(first["x1"] - first["x0"])
    if prev.x1 + 0.3 * line.size + first_width < right_edge - 1:
        return True  # the next word would have fitted on the previous line
    # Short items in narrow columns (skills, languages) rarely wrap.
    return (
        len(prev_text.split()) <= 4
        and text[:1].isupper()
        and not prev_text.endswith((",", "-", "&", "/", " and", " or"))
    )


def _lines_to_blocks(
    run: list[_Line], right_edge: float, body_size: float, page_no: int
) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    buf: list[str] = []
    buf_kind = BlockKind.PARAGRAPH
    prev: _Line | None = None
    row_x0 = 0.0  # x0 of the first part of the current row

    def flush() -> None:
        nonlocal buf
        if buf:
            blocks.append(TextBlock(kind=buf_kind, text=join_lines(buf), page=page_no))
        buf = []

    gaps = [b.top - a.bottom for a, b in zip(run, run[1:], strict=False) if b.top > a.bottom]
    para_gap = (statistics.median(gaps) * 1.8 + 1) if gaps else 6.0

    for line in run:
        # A glyph without a unicode mapping: a bullet at the start, else a decoration
        # (rating stars, icons); "(cid:N)" inside a word is real breakage and stays.
        text = _GLYPH_BULLET_RE.sub("• ", line.text)
        text = re.sub(r"(?:(?<=\s)|^)\(cid:\d+\)(?=\s|$)", "", text).strip()
        if not re.search(r"\w", text):
            continue  # a lone bullet, rule or icon
        words = len(text.split())
        # Bold alone is not enough: CVs bold job titles and employers, which are not sections.
        is_heading = is_section_keyword(text) or (line.size >= body_size * 1.2 and words <= 8)
        bullet, stripped = strip_bullet(text)
        same_row = prev is not None and abs(line.top - prev.top) <= LINE_TOLERANCE
        if is_heading:
            flush()
            blocks.append(TextBlock(kind=BlockKind.HEADING, text=tidy_heading(text), page=page_no))
            prev, row_x0 = line, line.x0
            continue
        if same_row and not bullet and buf_kind is not BlockKind.HEADING:
            buf.append(text)  # another part of the same row, e.g. date + role
            prev = line
            continue
        new_block = (
            prev is None
            or bullet
            or line.top - prev.bottom > para_gap
            or abs(line.size - prev.size) > 1.0
            or line.x0 < row_x0 - 5  # outdent: a new entry starts
            or _deliberate_break(prev, line, right_edge)
        )
        if new_block:
            flush()
            buf_kind = BlockKind.LIST_ITEM if bullet else BlockKind.PARAGRAPH
        buf.append(stripped if bullet else text)
        prev, row_x0 = line, line.x0
    flush()
    return blocks


def _running_lines(pages_lines: list[list[_Line]], heights: list[float]) -> set[str]:
    """Texts that repeat in the top/bottom margin of most pages (headers, footers, page nos)."""
    if len(pages_lines) < 2:
        return set()
    counts: Counter[str] = Counter()
    for lines, h in zip(pages_lines, heights, strict=True):
        seen = set()
        for ln in lines:
            if ln.top < h * MARGIN_FRACTION or ln.bottom > h * (1 - MARGIN_FRACTION):
                seen.add(re.sub(r"\d+", "#", ln.text))
        counts.update(seen)
    threshold = max(2, (len(pages_lines) + 1) // 2)
    return {t for t, n in counts.items() if n >= threshold}


def _image_share(page: Any) -> float:
    """Share of the page covered by images (overlaps counted twice; enough to spot a scan)."""
    area = sum(
        max(0.0, float(im["x1"] - im["x0"])) * max(0.0, float(im["bottom"] - im["top"]))
        for im in page.images
    )
    return area / max(1.0, float(page.width * page.height))


def _not_icon(obj: dict[str, Any]) -> bool:
    return obj.get("object_type") != "char" or not _ICON_FONT_RE.search(obj.get("fontname", ""))


def _open_error(path: Path, error: Exception) -> str:
    """A message a recruiter can act on, instead of the PDF library's."""
    cause = error.args[0] if error.args and isinstance(error.args[0], Exception) else error
    name = type(cause).__name__
    head = path.read_bytes()[:1024] if path.is_file() else b""
    if not head.strip():
        return "The file is empty."
    if name == "PDFPasswordIncorrect" or "password" in str(cause).lower():
        return "The PDF is password-protected; ask for a copy without a password."
    if b"%PDF" not in head:
        kind = (
            "a Word document or a zip file"
            if head.startswith((b"PK", b"\xd0\xcf\x11\xe0"))
            else "a web page"
            if b"<html" in head.lower() or b"<!doctype" in head.lower()
            else "an image"
            if head[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1")
            else "not a PDF"
        )
        return f"The file is named .pdf but is {kind}; rename it or ask for the original."
    if name in ("PSEOF", "PDFSyntaxError"):
        return "The PDF is damaged or incomplete (it may not have downloaded fully)."
    return f"Cannot open the PDF ({name}: {cause})."


def extract_pdf(path: Path, *, doc_type: DocType, display_path: str) -> SourceDocument:
    try:
        pdf = pdfplumber.open(path)
        _ = pdf.pages  # parses the document: password and structure errors surface here
    except Exception as e:
        raise ExtractionError(_open_error(path, e)) from e
    warnings: list[str] = []
    with pdf:
        pages_lines: list[list[_Line]] = []
        heights: list[float] = []
        widths: list[float] = []
        for i, original in enumerate(pdf.pages, start=1):
            page = original.filter(_not_icon)
            try:
                words = page.extract_words(
                    extra_attrs=["size"], keep_blank_chars=False, x_tolerance_ratio=WORD_GAP_RATIO
                )
            except Exception as e:  # noqa: BLE001 - one damaged page shouldn't lose the rest
                warnings.append(f"Page {i} could not be read ({type(e).__name__}).")
                words = []
            chars = sum(len(w["text"]) for w in words)
            if not words and page.images:
                warnings.append(f"Page {i} has no text layer (scanned image?).")
            elif chars < SCAN_PAGE_MAX_CHARS and _image_share(page) >= 0.3:
                # A scan with a little real text on top, e.g. a page number or a header
                # stamped by a PDF tool.
                warnings.append(f"Page {i} has little text and a large image (scanned page?).")
            elif not words and (page.curves or page.rects):
                warnings.append(
                    f"Page {i} has no text, only drawn shapes: the text may have been converted "
                    "to outlines (some design tools do this); ask for a PDF with real text."
                )
            pages_lines.append(_group_lines(words))
            heights.append(float(page.height))
            widths.append(float(page.width))
            # pdfplumber keeps every parsed page object until the file is closed: hundreds
            # of MB for a long PDF. Only the words are needed from here on.
            page.close()
            original.close()
        n_pages = len(pdf.pages)

    all_sizes = [w["size"] for lines in pages_lines for ln in lines for w in ln.words]
    body_size = statistics.median(all_sizes) if all_sizes else 10.0
    running = _running_lines(pages_lines, heights)

    # The text frame's right edge, measured before dropping running headers/footers: a
    # right-aligned page number often marks the margin better than the body text does.
    right_edge = max((ln.x1 for lines in pages_lines for ln in lines), default=0.0)
    pages_lines = [
        [ln for ln in lines if re.sub(r"\d+", "#", ln.text) not in running] for lines in pages_lines
    ]
    blocks: list[TextBlock] = []
    for page_no, (lines, width) in enumerate(zip(pages_lines, widths, strict=True), start=1):
        for run, edge in _reading_order(lines, width, right_edge):
            blocks.extend(_lines_to_blocks(run, edge, body_size, page_no))

    return build_document(
        doc_type=doc_type,
        path=path,
        display_path=display_path,
        blocks=blocks,
        extractor="pdfplumber",
        warnings=warnings,
        pages=n_pages,
    )
