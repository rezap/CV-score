"""Tests that need LibreOffice: real .doc/.odt conversion, and PDFs exported from Word files.

PDFs exported by an office suite (real line wrapping, embedded fonts, Symbol-font bullets)
are closer to what candidates send than the hand-placed PDFs in test_pdf.py.
"""

import subprocess
from pathlib import Path

import pytest

from cvscore.ingest import extract_file
from cvscore.ingest.document import sha256_file
from cvscore.ingest.legacy import find_soffice
from cvscore.models import BlockKind, DocType, SourceDocument
from tests.ingest import builders

SOFFICE = find_soffice()
pytestmark = pytest.mark.skipif(SOFFICE is None, reason="LibreOffice (soffice) not installed")


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    assert SOFFICE is not None
    d = tmp_path_factory.mktemp("libreoffice")
    source = builders.word_style_cv_docx(d / "alex.docx")
    out: dict[str, Path] = {"docx": source}
    for fmt in ("pdf", "doc", "odt"):
        subprocess.run(
            [SOFFICE, "--headless", "--convert-to", fmt, "--outdir", str(d), str(source)],
            check=True,
            capture_output=True,
            timeout=180,
        )
        out[fmt] = d / f"alex.{fmt}"
    return out


def outline(doc: SourceDocument) -> list[tuple[BlockKind, str]]:
    return [(b.kind, b.text[:40]) for b in doc.blocks]


EXPECTED = [
    (BlockKind.HEADING, "Alex Sample"),
    (BlockKind.PARAGRAPH, "alex.sample@example.org | +31 6 1234 567"),
    (BlockKind.HEADING, "Profile"),
    (BlockKind.PARAGRAPH, "Data scientist with seven years of exper"),
    (BlockKind.HEADING, "Experience"),
    (BlockKind.PARAGRAPH, "Lead Data Scientist, Example Logistics B"),
    (BlockKind.LIST_ITEM, "Designed a demand forecasting system tha"),
    (BlockKind.LIST_ITEM, "Hired and mentored four data scientists;"),
    (BlockKind.HEADING, "Education"),
    (BlockKind.PARAGRAPH, "MSc Econometrics, Erasmus University Rot"),
    (BlockKind.HEADING, "Skills"),
    (BlockKind.LIST_ITEM, "Python, SQL, Spark"),
    (BlockKind.LIST_ITEM, "Forecasting, causal inference"),
]


@pytest.mark.parametrize("fmt", ["docx", "doc", "odt", "pdf"])
def test_same_structure_from_every_format(converted: dict[str, Path], fmt: str) -> None:
    doc = extract_file(converted[fmt], doc_type=DocType.CV, display_path=f"alex.{fmt}")
    assert outline(doc) == EXPECTED
    assert doc.quality == 1.0
    assert doc.warnings == []


def test_pdf_wrapped_prose_is_one_paragraph(converted: dict[str, Path]) -> None:
    doc = extract_file(converted["pdf"], doc_type=DocType.CV, display_path="alex.pdf")
    profile = next(
        b for b in doc.blocks if b.section == "Profile" and b.kind is BlockKind.PARAGRAPH
    )
    assert profile.text.endswith(
        "from discovery to production monitoring. Fluent in Dutch and English."
    )
    bullet = next(b for b in doc.blocks if b.text.startswith("Designed"))
    assert bullet.text.endswith("while keeping inventory costs flat.")


def test_legacy_formats_keep_original_hash(converted: dict[str, Path]) -> None:
    doc = extract_file(converted["doc"], doc_type=DocType.CV, display_path="alex.doc")
    assert doc.extractor == "libreoffice+python-docx"
    assert doc.sha256 == sha256_file(converted["doc"])
