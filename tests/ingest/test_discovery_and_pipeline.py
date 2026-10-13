import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cvscore.cli import app
from cvscore.ingest import ingest, read_form_table
from cvscore.ingest.discovery import classify, discover_folder, discover_manifest, pseudonymous_id
from cvscore.models import DocType


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Jane_Doe_CV.pdf", DocType.CV),
        ("resume-2024.docx", DocType.CV),
        ("Lebenslauf.pdf", DocType.CV),
        ("cover_letter.docx", DocType.COVER_LETTER),
        ("Anschreiben.pdf", DocType.COVER_LETTER),
        ("application_form.json", DocType.APPLICATION_FORM),
        ("answers.yaml", DocType.APPLICATION_FORM),
        ("anything.csv", DocType.APPLICATION_FORM),
        ("Jane Doe.pdf", None),
    ],
)
def test_classify(name: str, expected: DocType | None) -> None:
    assert classify(Path(name)) is expected


def test_pseudonymous_id() -> None:
    cid = pseudonymous_id("Jane Doe")
    assert cid == pseudonymous_id(" jane doe ")
    assert cid.startswith("c-") and "jane" not in cid.lower()


def make_tree(root: Path, files: dict[str, Path]) -> None:
    (root / "Jane Doe").mkdir(parents=True)
    shutil.copy(files["two_column_pdf"], root / "Jane Doe" / "Jane Doe.pdf")  # unnamed -> CV
    shutil.copy(files["letter_pdf"], root / "Jane Doe" / "cover_letter.pdf")
    (root / "Jane Doe" / "~$cover_letter.docx").write_bytes(b"lock file")
    (root / "Jane Doe" / "form.json").write_text(json.dumps({"Notice period": "1 month"}))
    (root / "Sam Roe").mkdir()
    shutil.copy(files["cv_docx"], root / "Sam Roe" / "resume.docx")
    (root / "Sam Roe" / "broken.pdf").write_bytes(b"garbage")


def test_discover_folder(tmp_path: Path, files: dict[str, Path]) -> None:
    make_tree(tmp_path, files)
    sources = {s.label: s for s in discover_folder(tmp_path)}
    assert set(sources) == {"Jane Doe", "Sam Roe"}
    jane = {f.path.name: f.doc_type for f in sources["Jane Doe"].files}
    assert jane == {
        "Jane Doe.pdf": DocType.CV,
        "cover_letter.pdf": DocType.COVER_LETTER,
        "form.json": DocType.APPLICATION_FORM,
    }
    sam = {f.path.name: f.doc_type for f in sources["Sam Roe"].files}
    assert sam == {"resume.docx": DocType.CV, "broken.pdf": DocType.OTHER}


def test_discover_flat_folder(tmp_path: Path) -> None:
    for name in ["jane_doe_cv.pdf", "jane_doe_cover_letter.docx", "sam-resume.pdf"]:
        (tmp_path / name).write_bytes(b"")
    sources = {s.label: sorted(f.path.name for f in s.files) for s in discover_folder(tmp_path)}
    assert sources == {
        "Jane Doe": ["jane_doe_cover_letter.docx", "jane_doe_cv.pdf"],
        "Sam": ["sam-resume.pdf"],
    }


def test_discover_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.csv").write_text(
        "candidate,cv,cover_letter,form\nJane,docs/j.pdf,docs/j_letter.pdf,\nSam,docs/s.pdf,,\n"
    )
    sources = discover_manifest(tmp_path / "manifest.csv")
    assert [(s.label, [(f.path.name, f.doc_type) for f in s.files]) for s in sources] == [
        ("Jane", [("j.pdf", DocType.CV), ("j_letter.pdf", DocType.COVER_LETTER)]),
        ("Sam", [("s.pdf", DocType.CV)]),
    ]


def test_ingest_end_to_end(tmp_path: Path, files: dict[str, Path]) -> None:
    root = tmp_path / "candidates"
    make_tree(root, files)
    result = ingest(root)
    assert sorted(result.identity.values()) == ["Jane Doe", "Sam Roe"]
    by_label = {result.identity[c.candidate_id]: c for c in result.candidates}

    jane = by_label["Jane Doe"]
    assert sorted(d.doc_type for d in jane.documents) == [
        DocType.APPLICATION_FORM,
        DocType.COVER_LETTER,
        DocType.CV,
    ]
    assert {d.path for d in jane.documents} == {
        "Jane Doe/Jane Doe.pdf",
        "Jane Doe/cover_letter.pdf",
        "Jane Doe/form.json",
    }
    assert jane.errors == []

    sam = by_label["Sam Roe"]
    assert [d.doc_type for d in sam.documents] == [DocType.CV]
    assert len(sam.errors) == 1 and sam.errors[0].startswith("Sam Roe/broken.pdf:")


def test_ingest_with_form_table(tmp_path: Path, files: dict[str, Path]) -> None:
    root = tmp_path / "candidates"
    make_tree(root, files)
    export = tmp_path / "export.csv"
    export.write_text("Folder,Why us\nSam Roe,Mission\n")
    result = ingest(root, form_table=read_form_table(export, "Folder"), form_table_path=export)
    sam = next(c for c in result.candidates if result.identity[c.candidate_id] == "Sam Roe")
    form = next(d for d in sam.documents if d.doc_type is DocType.APPLICATION_FORM)
    assert form.extractor == "form:table"
    assert "Mission" in form.text


def test_cli_ingest(tmp_path: Path, files: dict[str, Path]) -> None:
    root = tmp_path / "candidates"
    make_tree(root, files)
    out = tmp_path / "out"
    result = CliRunner().invoke(app, ["ingest", str(root), "--out", str(out)])
    assert result.exit_code == 0, result.output
    identity = json.loads((out / "identity_map.json").read_text())
    assert sorted(identity.values()) == ["Jane Doe", "Sam Roe"]
    written = sorted(p.stem for p in (out / "candidates").glob("*.json"))
    assert written == sorted(identity)
    for path in (out / "candidates").glob("*.json"):
        assert "Jane Doe" not in path.name and "Sam Roe" not in path.name
    assert "x Sam Roe/broken.pdf" in result.output
    assert "keep it private" in result.output


def test_cli_forms_needs_key(tmp_path: Path) -> None:
    export = tmp_path / "export.csv"
    export.write_text("a\n1\n")
    result = CliRunner().invoke(app, ["ingest", str(tmp_path), "--forms", str(export)])
    assert result.exit_code != 0
    assert "--form-key" in result.output
