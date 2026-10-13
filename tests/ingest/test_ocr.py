"""OCR of scanned pages and image files with a (fake) vision model."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from cvscore.config import ModelRole, OCRConfig
from cvscore.ingest import ingest
from cvscore.ingest.discovery import discover_folder
from cvscore.ingest.ocr import PageTranscript, llm_page_reader, ocr_hook, with_ocr
from cvscore.ingest.pipeline import extract_file
from cvscore.models import BlockKind, DocType, SourceDocument
from cvscore.redact import redact
from tests.ingest import builders

TRANSCRIPT = """Kari Nordmann
kari.nordmann@example.no | +47 98 76 54 32

## Experience
- Senior Data Scientist, Equinor, 2019 - present
- Built forecasting models for 40 offshore platforms, reducing downtime by 12%.

## Education
MSc Statistics, NTNU, 2013 - 2015
"""


def extract(path: Path) -> SourceDocument:
    return extract_file(path, doc_type=DocType.OTHER, display_path=path.name)


class Reader:
    """Records the page images it is given and returns a fixed transcript."""

    def __init__(self, text: str = TRANSCRIPT) -> None:
        self.text = text
        self.pages: list[bytes] = []

    def __call__(self, png: bytes) -> str:
        self.pages.append(png)
        return self.text


def test_scanned_pdf_is_read_by_ocr(tmp_path: Path) -> None:
    path = builders.scanned_pdf(tmp_path / "scan.pdf")
    doc = extract(path)
    assert doc.text == "" and any("no text layer" in w for w in doc.warnings)

    reader = Reader()
    doc = with_ocr(doc, path, reader, OCRConfig())
    assert len(reader.pages) == 1 and reader.pages[0].startswith(b"\x89PNG")
    assert doc.extractor == "pdfplumber+ocr"
    assert "Built forecasting models for 40 offshore platforms" in doc.text
    assert [b.text for b in doc.blocks if b.kind is BlockKind.HEADING] == [
        "Experience",
        "Education",
    ]
    assert all(b.page == 1 for b in doc.blocks)
    assert doc.quality > 0.5
    assert not any("no text layer" in w or "Very little text" in w for w in doc.warnings)
    assert any(w.startswith("Read by OCR (page 1)") for w in doc.warnings)


def test_only_pages_without_text_are_read(tmp_path: Path) -> None:
    path = builders.text_then_scan_pdf(tmp_path / "cv.pdf")
    reader = Reader("## Certificates\nAzure AI Engineer Associate")
    doc = with_ocr(extract(path), path, reader, OCRConfig())
    assert len(reader.pages) == 1
    assert doc.text.index("Senior Data Scientist") < doc.text.index("Azure AI Engineer")
    assert {b.page for b in doc.blocks if "Azure" in b.text} == {2}


def test_documents_with_text_are_left_alone(tmp_path: Path, files: dict[str, Path]) -> None:
    doc = extract(files["two_column_pdf"])
    reader = Reader()
    assert with_ocr(doc, files["two_column_pdf"], reader, OCRConfig()) is doc
    assert reader.pages == []


def test_max_pages(tmp_path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    path = tmp_path / "long-scan.pdf"
    c = Canvas(str(path), pagesize=A4)
    for _ in range(5):
        c.rect(50, 50, 100, 100, fill=1)
        c.showPage()
    c.save()
    reader = Reader()
    with_ocr(extract(path), path, reader, OCRConfig(max_pages=2))
    assert len(reader.pages) == 2


def test_ocr_failure_keeps_the_document(tmp_path: Path) -> None:
    path = builders.scanned_pdf(tmp_path / "scan.pdf")

    def broken(_: bytes) -> str:
        raise TimeoutError("model timed out")

    doc = with_ocr(extract(path), path, broken, OCRConfig())
    assert doc.text == ""
    assert "OCR failed (TimeoutError: model timed out)" in doc.warnings[-1]


@pytest.mark.parametrize("name", ["Kari_Nordmann_CV.png", "kari-cv.jpg"])
def test_image_files_named_as_cvs(tmp_path: Path, name: str) -> None:
    builders.scanned_cv_image(tmp_path / name)
    builders.scanned_cv_image(tmp_path / "portrait.jpg")  # a photo: never sent anywhere
    reader = Reader()
    hook = ocr_hook_from(reader)
    [source] = discover_folder(tmp_path, ocr=hook)
    assert [f.display_path for f in source.files] == [name]
    assert source.files[0].doc_type is DocType.CV
    assert source.label == "Kari Nordmann"
    assert len(reader.pages) == 1


def test_image_without_ocr_says_so(tmp_path: Path) -> None:
    builders.scanned_cv_image(tmp_path / "Kari_CV.png")
    [source] = discover_folder(tmp_path)
    [f] = source.files
    assert f.document is not None and f.document.text == ""
    assert any("can only be read with OCR" in w for w in f.document.warnings)


def ocr_hook_from(reader: Reader) -> Any:
    def hook(path: Path, doc: SourceDocument) -> SourceDocument:
        return with_ocr(doc, path, reader, OCRConfig())

    return hook


def test_ocr_text_is_grouped_and_redacted(tmp_path: Path) -> None:
    builders.scanned_pdf(tmp_path / "scan.pdf")  # no name in the file name
    result = ingest(tmp_path, ocr=ocr_hook_from(Reader()))
    [cand] = result.candidates
    assert result.identity[cand.candidate_id] == "Kari Nordmann"
    [doc] = cand.documents
    assert doc.doc_type is DocType.CV
    text = redact(cand, result.names[cand.candidate_id])[DocType.CV]
    assert "Kari" not in text and "example.no" not in text and "98 76" not in text
    assert "Built forecasting models for 40 offshore platforms" in text


class RecordingLLM:
    model_id = "fake-vision"

    def __init__(self) -> None:
        self.calls: list[tuple[list[BaseMessage], RunnableConfig | None]] = []

    def generate(
        self, schema: type[Any], messages: Sequence[BaseMessage], config: RunnableConfig | None
    ) -> Any:
        self.calls.append((list(messages), config))
        return schema(text="## Experience\n- Data Scientist")


def test_llm_page_reader_sends_the_image_untraced() -> None:
    llm = RecordingLLM()
    roles: list[ModelRole] = []

    def provider(role: ModelRole) -> RecordingLLM:
        roles.append(role)
        return llm

    read = llm_page_reader(provider, OCRConfig(role=ModelRole.SCORER))
    assert read(b"\x89PNG fake") == "## Experience\n- Data Scientist"
    [(messages, config)] = llm.calls
    assert roles == [ModelRole.SCORER]
    assert config is None  # no callbacks: neither image nor transcript is traced
    image = messages[-1].content[1]
    assert image["type"] == "image_url"
    assert image["image_url"]["url"].startswith("data:image/png;base64,")


def test_ocr_can_be_switched_off() -> None:
    assert ocr_hook(lambda role: RecordingLLM(), OCRConfig(enabled=False)) is None
    assert PageTranscript(text="x").text == "x"


def test_scanned_page_with_a_little_real_text_is_read(tmp_path: Path) -> None:
    path = builders.scan_with_text_footer_pdf(tmp_path / "cv.pdf")
    doc = extract(path)
    assert any(w.startswith("Page 2 has little text and a large image") for w in doc.warnings)
    reader = Reader("## Certificates\nAzure AI Engineer Associate")
    doc = with_ocr(doc, path, reader, OCRConfig())
    assert len(reader.pages) == 1  # only page 2
    assert "Senior Data Scientist" in doc.text and "Azure AI Engineer Associate" in doc.text
    assert "Page 2 of 2" not in doc.text  # the page's own text is replaced by the transcript
