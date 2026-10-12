from pathlib import Path

import pytest

from cvscore.ingest import ExtractionError, extract_file
from cvscore.models import BlockKind, DocType, SourceDocument


@pytest.fixture(scope="module")
def doc(files: dict[str, Path]) -> SourceDocument:
    return extract_file(files["cv_docx"], doc_type=DocType.CV, display_path="cv.docx")


def kinds_and_texts(doc: SourceDocument) -> list[tuple[BlockKind, str]]:
    return [(b.kind, b.text) for b in doc.blocks]


def test_structure(doc: SourceDocument) -> None:
    assert kinds_and_texts(doc) == [
        (BlockKind.PARAGRAPH, "Jane Example | jane@example.com | +44 20 7946 0958"),
        (BlockKind.PARAGRAPH, "Jane Example"),
        (BlockKind.PARAGRAPH, "Skills: Python, SQL, Spark"),
        (BlockKind.HEADING, "Experience"),
        (BlockKind.PARAGRAPH, "Senior Data Engineer, Acme Analytics Ltd, 2019 - 2024"),
        (BlockKind.LIST_ITEM, "Led a team of 5 engineers on the streaming platform."),
        (BlockKind.LIST_ITEM, "Reduced pipeline failures by 40%."),
        (BlockKind.PARAGRAPH, "Data Engineer, Northwind Traders"),
        (BlockKind.LIST_ITEM, "Built ETL in Python"),
        (BlockKind.LIST_ITEM, "Ran Airflow"),
        (BlockKind.HEADING, "EDUCATION"),
        (BlockKind.PARAGRAPH, "MSc Computer Science, University of Exampleton, 2016"),
        (BlockKind.HEADING, "Languages"),
        (BlockKind.TABLE_ROW, "Language | Level"),
        (BlockKind.TABLE_ROW, "English | Native"),
        (BlockKind.TABLE_ROW, "German | B2"),
    ]


def test_text_box_read_once(doc: SourceDocument) -> None:
    # Word stores text boxes twice (modern + legacy fallback); only one copy is kept.
    assert doc.text.count("Skills: Python, SQL, Spark") == 1


def test_header_is_contact(doc: SourceDocument) -> None:
    header = doc.blocks[0]
    assert header.is_contact
    assert header.section == "Header/footer"


def test_contact_flags_and_sections(doc: SourceDocument) -> None:
    by_text = {b.text: b for b in doc.blocks}
    assert by_text["Jane Example"].is_contact
    assert not by_text["Skills: Python, SQL, Spark"].is_contact
    assert by_text["Ran Airflow"].section == "Experience"
    assert by_text["German | B2"].section == "Languages"


def test_layout_table_read_cell_by_cell(files: dict[str, Path]) -> None:
    doc = extract_file(files["layout_docx"], doc_type=DocType.CV, display_path="layout.docx")
    texts = [b.text for b in doc.blocks]
    assert texts.index("SKILLS") < texts.index("Spark") < texts.index("EXPERIENCE")
    assert {b.section for b in doc.blocks if b.text in ("Python", "SQL")} == {"SKILLS"}
    assert not any(b.kind is BlockKind.TABLE_ROW for b in doc.blocks)


def test_corrupt_docx_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"PK\x03\x04 broken zip")
    with pytest.raises(ExtractionError, match="Not a valid .docx"):
        extract_file(bad, doc_type=DocType.CV, display_path="bad.docx")
