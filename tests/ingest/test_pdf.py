from pathlib import Path

import pytest

from cvscore.ingest import ExtractionError, extract_file
from cvscore.models import BlockKind, DocType, SourceDocument, TextBlock


def extract(path: Path, doc_type: DocType = DocType.CV) -> SourceDocument:
    return extract_file(path, doc_type=doc_type, display_path=path.name)


def find(doc: SourceDocument, text: str) -> TextBlock:
    matches = [b for b in doc.blocks if text in b.text]
    assert len(matches) == 1, f"expected one block containing {text!r}, got {matches}"
    return matches[0]


def headings(doc: SourceDocument) -> list[str]:
    return [b.text for b in doc.blocks if b.kind is BlockKind.HEADING]


@pytest.fixture(scope="module")
def doc(files: dict[str, Path]) -> SourceDocument:
    return extract(files["two_column_pdf"])


class TestTwoColumnCV:
    def test_reads_sidebar_before_main_column_on_each_page(self, doc: SourceDocument) -> None:
        assert headings(doc) == [
            "Jane Example",
            "Senior Data Engineer",
            "CONTACT",
            "SKILLS",
            "LANGUAGES",
            "EXPERIENCE",
            "CERTIFICATIONS",
            "EDUCATION",
        ]

    def test_columns_are_not_interleaved(self, doc: SourceDocument) -> None:
        for b in doc.blocks:
            assert not ("CONTACT" in b.text and "EXPERIENCE" in b.text)
            assert not ("AWS" in b.text and "MSc" in b.text)

    def test_sections_and_pages(self, doc: SourceDocument) -> None:
        assert find(doc, "Apache Spark").section == "SKILLS"
        assert find(doc, "Led a team of 5").section == "EXPERIENCE"
        assert find(doc, "Databricks Associate").section == "CERTIFICATIONS"
        assert find(doc, "MSc Computer Science").section == "EDUCATION"
        assert find(doc, "Apache Spark").page == 1
        assert find(doc, "MSc Computer Science").page == 2

    def test_bullets_without_unicode_mapping_become_list_items(self, doc: SourceDocument) -> None:
        assert find(doc, "Apache Spark").kind is BlockKind.LIST_ITEM
        assert find(doc, "Apache Spark").text == "Apache Spark"
        assert "(cid:" not in doc.text
        assert doc.quality == 1.0
        assert doc.warnings == []

    def test_wrapped_lines_are_joined_and_dehyphenated(self, doc: SourceDocument) -> None:
        bullet = find(doc, "Introduced data-quality")
        assert bullet.text == (
            "Introduced data-quality monitoring and incident management, "
            "reducing pipeline failures by 40%."
        )

    def test_short_lines_stay_separate(self, doc: SourceDocument) -> None:
        assert find(doc, "English (native)").text == "English (native)"
        assert find(doc, "AWS Data Analytics").text == "AWS Data Analytics"
        assert find(doc, "MSc Computer Science").text.endswith("2016")

    def test_running_header_and_footer_removed(self, doc: SourceDocument) -> None:
        assert "Page 1 of 2" not in doc.text
        assert "Curriculum Vitae" not in doc.text

    def test_contact_flags(self, doc: SourceDocument) -> None:
        assert find(doc, "jane@example.com").is_contact
        assert find(doc, "+44 20 7946 0958").is_contact
        assert next(b for b in doc.blocks if b.text == "Jane Example").is_contact
        # A date range is not a phone number.
        assert not find(doc, "Northwind Traders, 2016 - 2019").is_contact
        assert not find(doc, "Led a team of 5").is_contact

    def test_markdown_rendering(self, doc: SourceDocument) -> None:
        assert "## SKILLS\n\n- Python\n- SQL and dbt\n- Apache Spark" in doc.text


def test_date_column_is_read_row_by_row(files: dict[str, Path]) -> None:
    doc = extract(files["date_column_pdf"])
    texts = [b.text for b in doc.blocks]
    assert texts == [
        "Sam Placeholder",
        "Experience",
        "2019 - 2024 Product Manager, Contoso Ltd",
        "Launched a pricing feature used by 30% of customers.",
        "2015 - 2019 Business Analyst, Fabrikam Inc",
        "Built the weekly sales forecast used by the board.",
        "Education",
        "2011 - 2015 BA Economics, University of Exampleton",
    ]


def test_single_column_letter(files: dict[str, Path]) -> None:
    doc = extract(files["letter_pdf"], DocType.COVER_LETTER)
    assert [b.text[:20] for b in doc.blocks] == [
        "Dear Hiring Manager,",
        "I am writing to appl",
        "Kind regards,",
    ]
    # The top-of-page identity rule applies to CVs only.
    assert not any(b.is_contact for b in doc.blocks)


def test_scanned_pdf_is_flagged(files: dict[str, Path]) -> None:
    doc = extract(files["scanned_pdf"])
    assert doc.quality <= 0.1
    assert any("no text layer" in w for w in doc.warnings)


def test_corrupt_pdf_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(ExtractionError):
        extract(bad)
