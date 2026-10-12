import json
from pathlib import Path

import pytest

from cvscore.ingest import ExtractionError, extract_file, read_form_table
from cvscore.ingest.forms import read_form_file
from cvscore.models import BlockKind, DocType


def qa(path: Path) -> list[tuple[str, str]]:
    return [(a.question, a.answer) for a in read_form_file(path)]


def test_json_mapping_with_mixed_values(tmp_path: Path) -> None:
    f = tmp_path / "form.json"
    f.write_text(
        json.dumps(
            {
                "Why this role?": "I enjoy building data platforms.",
                "Years of experience": 8,
                "Willing to relocate": True,
                "Tools": ["Python", "dbt"],
                "Empty": "",
            }
        )
    )
    assert qa(f) == [
        ("Why this role?", "I enjoy building data platforms."),
        ("Years of experience", "8"),
        ("Willing to relocate", "Yes"),
        ("Tools", "Python; dbt"),
    ]


def test_json_list_and_yaml(tmp_path: Path) -> None:
    j = tmp_path / "a.json"
    j.write_text(json.dumps([{"Question": "Notice period", "Answer": "1 month"}]))
    y = tmp_path / "a.yaml"
    y.write_text("Notice period: 1 month\nSalary expectation: negotiable\n")
    assert qa(j) == [("Notice period", "1 month")]
    assert qa(y) == [("Notice period", "1 month"), ("Salary expectation", "negotiable")]


def test_csv_shapes(tmp_path: Path) -> None:
    pairs = tmp_path / "pairs.csv"
    pairs.write_text("question,answer\nNotice period,1 month\n")
    row = tmp_path / "row.csv"
    row.write_text("Notice period,Why us\n1 month,Great team\n")
    many = tmp_path / "many.csv"
    many.write_text("Notice period\n1 month\n2 months\n")
    assert qa(pairs) == [("Notice period", "1 month")]
    assert qa(row) == [("Notice period", "1 month"), ("Why us", "Great team")]
    with pytest.raises(ExtractionError, match="--form-key"):
        read_form_file(many)


def test_invalid_form(tmp_path: Path) -> None:
    f = tmp_path / "form.json"
    f.write_text("{not json")
    with pytest.raises(ExtractionError, match="Cannot parse"):
        read_form_file(f)
    f.write_text('"just a string"')
    with pytest.raises(ExtractionError, match="mapping"):
        read_form_file(f)


def test_form_document_flags_personal_fields(tmp_path: Path) -> None:
    f = tmp_path / "application_form.json"
    f.write_text(json.dumps({"Email address": "jane@example.com", "Why us?": "Mission."}))
    doc = extract_file(f, doc_type=DocType.APPLICATION_FORM, display_path=f.name)
    assert doc.doc_type is DocType.APPLICATION_FORM
    assert doc.quality == 1.0 and doc.warnings == []
    assert doc.text == "## Email address\n\njane@example.com\n\n## Why us?\n\nMission."
    by_text = {b.text: b for b in doc.blocks}
    assert by_text["jane@example.com"].is_contact
    assert not by_text["Mission."].is_contact
    assert by_text["Mission."].section == "Why us?"
    assert by_text["Why us?"].kind is BlockKind.HEADING


def test_form_table(tmp_path: Path) -> None:
    f = tmp_path / "export.csv"
    f.write_text("Applicant,Notice period,Why us\njane_doe,1 month,Team\nsam,,Mission\n")
    table = read_form_table(f, "Applicant")
    assert set(table) == {"jane_doe", "sam"}
    assert [(a.question, a.answer) for a in table["sam"]] == [("Why us", "Mission")]
    with pytest.raises(ExtractionError, match="Column 'Email' not found"):
        read_form_table(f, "Email")
