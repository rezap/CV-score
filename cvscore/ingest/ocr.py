"""OCR for scanned CVs: page images transcribed by a vision-capable chat model.

A PDF page without a text layer (a scan, or text turned into outlines) and an image file
(a photo of a CV) carry no text for the extractors. ``with_ocr`` renders those pages and
asks the model to transcribe them as light markdown, which is then read like any text
document: headings, list items, contact details (flagged for redaction), quality score.

Privacy: the model sees the page image before redaction. Calls are made without
callbacks, so neither the image nor the transcript reaches Langfuse. Results are cached
like other model calls, so a page is only read once.
"""

from __future__ import annotations

import base64
import io
import re
from collections.abc import Callable
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from cvscore.config import OCRConfig
from cvscore.ingest.document import MIN_CHARS, ExtractionError, build_document
from cvscore.ingest.plaintext import blocks_from_text
from cvscore.llm.structured import LLMProvider
from cvscore.models import DocType, SourceDocument

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"})
MAX_IMAGE_SIDE = 2000  # pixels; enough for small print, keeps requests small

# Reads one page image (PNG bytes) and returns its text as light markdown.
PageReader = Callable[[bytes], str]

_NO_TEXT_PAGE_RE = re.compile(r"^Page (\d+) has no text")

OCR_SYSTEM = """\
You transcribe one page of a job application (CV, cover letter or form) from an image.

Rules:
- Copy all text exactly as written, in its original language. Do not translate, correct, \
summarise or add anything.
- Reading order: in a multi-column layout, read the left column top to bottom, then the \
next column.
- Markdown: "## " before section headings, "- " before list items, a blank line between \
paragraphs. Keep dates, names, emails and phone numbers as written.
- Skip decorations (icons, skill bars, photos, logos). If the page has no text, return an \
empty string.
"""


class PageTranscript(BaseModel):
    text: str = Field(description="The page's text as light markdown, exactly as written.")


def llm_page_reader(llm: LLMProvider, config: OCRConfig) -> PageReader:
    """A page reader backed by the vision model of ``config.role``."""

    def read(png: bytes) -> str:
        image = base64.b64encode(png).decode("ascii")
        messages = [
            SystemMessage(OCR_SYSTEM),
            HumanMessage(
                content=[
                    {"type": "text", "text": "Transcribe this page."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}},
                ]
            ),
        ]
        # No config: no callbacks, so nothing from this call is traced.
        return llm(config.role).generate(PageTranscript, messages, None).text

    return read


def _png(image: object) -> bytes:
    from PIL import Image

    assert isinstance(image, Image.Image)
    image = image.convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_pages(path: Path, pages: list[int], dpi: int) -> dict[int, bytes]:
    """PNG images of the given 1-based ``pages`` of a PDF, or of an image file (page 1)."""
    if path.suffix.lower() in IMAGE_SUFFIXES:
        from PIL import Image

        try:
            with Image.open(path) as img:
                return {1: _png(img)}
        except OSError as e:
            raise ExtractionError(f"Cannot open the image: {e}") from e
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        return {n: _png(pdf[n - 1].render(scale=dpi / 72).to_pil()) for n in pages if n <= len(pdf)}
    finally:
        pdf.close()


def pages_without_text(doc: SourceDocument, path: Path, max_pages: int) -> list[int]:
    """1-based pages that need OCR: pages flagged as having no text, or every page (up to
    ``max_pages``) when the whole document has almost no text."""
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return [1]
    if path.suffix.lower() != ".pdf":
        return []
    flagged = sorted(
        int(m.group(1)) for w in doc.warnings if (m := _NO_TEXT_PAGE_RE.match(w)) is not None
    )
    if flagged:
        return flagged[:max_pages]
    if len(doc.text.strip()) < MIN_CHARS:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            return list(range(1, min(len(pdf), max_pages) + 1))
        finally:
            pdf.close()
    return []


def image_document(path: Path, *, doc_type: DocType, display_path: str) -> SourceDocument:
    """An image file before OCR: no text yet."""
    return build_document(
        doc_type=doc_type,
        path=path,
        display_path=display_path,
        blocks=[],
        extractor="image",
        warnings=["Image file: its text can only be read with OCR (ocr.enabled in the config)."],
    )


def with_ocr(
    doc: SourceDocument, path: Path, read_page: PageReader, config: OCRConfig
) -> SourceDocument:
    """``doc`` with OCR text for its pages that have none; unchanged if none need it."""
    pages = pages_without_text(doc, path, config.max_pages)
    if not pages:
        return doc
    try:
        images = render_pages(path, pages, config.dpi)
        transcripts = {n: read_page(png) for n, png in images.items()}
    except Exception as e:  # noqa: BLE001 - keep the document, say why OCR failed
        doc.warnings.append(f"OCR failed ({type(e).__name__}: {e}); the text may be incomplete.")
        return doc
    blocks = [b for b in doc.blocks if b.page not in transcripts]
    for n, text in transcripts.items():
        blocks.extend(blocks_from_text(text, page=n))
    blocks.sort(key=lambda b: b.page or 0)
    kept_warnings = [
        w for w in doc.warnings if not (_NO_TEXT_PAGE_RE.match(w) or w.startswith("Image file"))
    ]
    kept_warnings = [w for w in kept_warnings if not w.startswith("Very little text")]
    pages_note = ", ".join(str(n) for n in transcripts)
    ocr_doc = build_document(
        doc_type=doc.doc_type,
        path=path,
        display_path=doc.path,
        blocks=blocks,
        extractor=f"{doc.extractor}+ocr",
        warnings=[
            *kept_warnings,
            f"Read by OCR (page {pages_note}): check names, numbers and quotes against "
            "the original.",
        ],
        pages=None,
    )
    return ocr_doc


def ocr_hook(
    llm: LLMProvider, config: OCRConfig
) -> Callable[[Path, SourceDocument], SourceDocument] | None:
    """The OCR step for ingestion, or None when OCR is switched off."""
    if not config.enabled:
        return None
    read = llm_page_reader(llm, config)

    def hook(path: Path, doc: SourceDocument) -> SourceDocument:
        return with_ocr(doc, path, read, config)

    return hook
