from pathlib import Path

import pytest

from cvscore.ingest import ExtractionError, extract_file, legacy
from cvscore.ingest.text_utils import (
    is_section_keyword,
    join_lines,
    normalize_text,
    strip_bullet,
    tidy_heading,
)
from cvscore.models import BlockKind, DocType


def test_normalize_text() -> None:
    assert normalize_text("  e\ufb03cient\u00a0 work\u200d ") == "efficient work"
    assert normalize_text("\u201cquoted\u201d it\u2019s") == '"quoted" it\'s'


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("• Python", (True, "Python")),
        ("- SQL", (True, "SQL")),
        ("o Spark", (True, "Spark")),
        ("➢ Airflow", (True, "Airflow")),
        ("\uf0b7 dbt", (True, "dbt")),  # Word's Symbol-font bullet in exported PDFs
        ("\u2022Developed models", (True, "Developed models")),  # no space after the bullet
        ("* Spark", (True, "Spark")),
        ("*Note", (False, "*Note")),
        ("-5% churn", (False, "-5% churn")),
        ("Oracle DBA", (False, "Oracle DBA")),
    ],
)
def test_strip_bullet(line: str, expected: tuple[bool, str]) -> None:
    assert strip_bullet(line) == expected


def test_join_lines() -> None:
    assert join_lines(["incident manage-", "ment and more"]) == "incident management and more"
    assert join_lines(["ends with year 2019-", "2024"]) == "ends with year 2019- 2024"
    assert join_lines(["first line", "", "second"]) == "first line second"


def test_text_and_markdown_files(tmp_path: Path) -> None:
    f = tmp_path / "cv.md"
    f.write_text(
        "# Jane Example\n\nExperience\n- Led a team\n* Shipped things\n\nWrapped para\ncontinues\n",
        encoding="utf-8",
    )
    doc = extract_file(f, doc_type=DocType.CV, display_path="cv.md")
    assert [(b.kind, b.text) for b in doc.blocks] == [
        (BlockKind.HEADING, "Jane Example"),
        (BlockKind.HEADING, "Experience"),
        (BlockKind.LIST_ITEM, "Led a team"),
        (BlockKind.LIST_ITEM, "Shipped things"),
        (BlockKind.PARAGRAPH, "Wrapped para continues"),
    ]


def test_windows_1252_text(tmp_path: Path) -> None:
    f = tmp_path / "letter.txt"
    f.write_bytes("Café résumé".encode("cp1252"))
    doc = extract_file(f, doc_type=DocType.COVER_LETTER, display_path="letter.txt")
    assert doc.blocks[0].text == "Café résumé"


def test_legacy_without_libreoffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy, "find_soffice", lambda: None)
    f = tmp_path / "old.doc"
    f.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(ExtractionError, match="LibreOffice"):
        extract_file(f, doc_type=DocType.CV, display_path="old.doc")


def test_unsupported_and_missing(tmp_path: Path) -> None:
    f = tmp_path / "notes.xyz"
    f.write_bytes(b"\xff\xd8")
    with pytest.raises(ExtractionError, match="Unsupported"):
        extract_file(f, doc_type=DocType.OTHER, display_path="notes.xyz")
    with pytest.raises(ExtractionError, match="not found"):
        extract_file(tmp_path / "nope.pdf", doc_type=DocType.CV, display_path="nope.pdf")


@pytest.mark.parametrize(
    ("line", "keyword", "tidy"),
    [
        ("edu cation", True, "education"),  # first letters in another colour
        ("E X P E R I E N C E", True, "EXPERIENCE"),
        ("ABOUTME", True, "ABOUT ME"),
        ("Work Experience:", True, "Work Experience"),
        ("Educated guess", False, "Educated guess"),
    ],
)
def test_split_section_names(line: str, keyword: bool, tidy: str) -> None:
    assert is_section_keyword(line) is keyword
    assert tidy_heading(line) == tidy
