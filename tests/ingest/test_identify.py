from pathlib import Path

import pytest

from cvscore.ingest import ingest
from cvscore.ingest.discovery import discover_folder
from cvscore.ingest.identify import (
    filename_key,
    looks_like_name,
    name_from_filename,
    same_person,
    type_from_filename,
)
from cvscore.models import DocType


def cv_text(name: str, email: str) -> str:
    return f"""{name}

Oslo | {email} | +47 98 76 54 32

## Summary

Data scientist with eight years of experience in forecasting and computer vision.

## Experience

Senior Data Scientist, Northwind, 2019 - present

- Built a demand forecasting service used by 40 stores.
- Led a team of four engineers.

Data Analyst, Contoso, 2015 - 2019

- Automated weekly reporting with Python and SQL.

## Education

MSc Statistics, University of Oslo, 2013 - 2015

## Skills

- Python, SQL, PyTorch
"""


def letter_text(name: str, email: str | None = None, *, greeting: str = "Dear hiring team,") -> str:
    contact = f"\n\n{email}" if email else ""
    return f"""{greeting}

I am writing to apply for the Senior Data Scientist position at your company. In my current
role I have built forecasting services that store managers use every day, and I have led a
small team of engineers through several releases while keeping the models reliable.

I would welcome the chance to bring this experience to your team and to learn from the
domain experts you work with. I enjoy explaining models to people who use them.

Kind regards,

{name}{contact}
"""


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def grouped(root: Path) -> dict[str, dict[str, DocType]]:
    return {s.label: {f.display_path: f.doc_type for f in s.files} for s in discover_folder(root)}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Kari_Nordmann_CV.pdf", DocType.CV),
        ("JaneDoe_CoverLetter.docx", DocType.COVER_LETTER),
        ("Søknad - Kari Nordmann.pdf", DocType.COVER_LETTER),
        ("soknadsbrev.docx", DocType.COVER_LETTER),
        ("Lebenslauf.pdf", DocType.CV),
        ("application.pdf", None),  # could be a letter or a form: the content decides
        ("Jane Doe.pdf", None),
    ],
)
def test_type_from_filename(name: str, expected: DocType | None) -> None:
    assert type_from_filename(Path(name)) is expected


@pytest.mark.parametrize(
    ("name", "key", "person"),
    [
        ("Jane_Doe_CV_final_v2.pdf", "jane doe", "Jane Doe"),
        ("Cover letter - Jane Doe (1).docx", "jane doe", "Jane Doe"),
        ("CV_2024.pdf", "", None),
        ("12345_cv.pdf", "12345", None),
        ("Senior_Data_Scientist_CV.pdf", "senior data scientist", None),
    ],
)
def test_filename_key_and_name(name: str, key: str, person: str | None) -> None:
    assert filename_key(Path(name)) == key
    assert name_from_filename(Path(name)) == person


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Jane Doe", True),
        ("JANE DOE", True),
        ("Anne-Marie van der Berg", True),
        ("J. R. Tolkien", True),
        ("Senior Data Scientist", False),
        ("Curriculum Vitae", False),
        ("Oslo, Norway", False),
        ("Work Experience", False),
        ("Jane", False),
    ],
)
def test_looks_like_name(text: str, expected: bool) -> None:
    assert looks_like_name(text) is expected


def test_same_person() -> None:
    assert same_person("Jane Doe", "JANE DOE")
    assert same_person("Jane A. Doe", "Jane Doe")
    assert same_person("Søren Ødegård", "Soren Odegard")
    assert not same_person("Jane Doe", "John Doe")
    assert not same_person("Jane", "Jane Doe")


def test_flat_folder_matches_letters_to_cvs(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            # name in the file names
            "kari_nordmann_cv.txt": cv_text("Kari Nordmann", "kari@example.no"),
            "Cover letter - Kari Nordmann.txt": letter_text("Kari Nordmann"),
            # no name in the file names: the CV's top line and the letter's signature
            "CV_final.txt": cv_text("OLA HANSEN", "ola.h@example.com"),
            "application.txt": letter_text("Ola Hansen"),
            # different spelling in the signature, same email
            "resume (3).txt": cv_text("Per Olsen", "per.olsen@example.com"),
            "document1.txt": letter_text("P. Olsen", "per.olsen@example.com"),
            # a letter without a CV
            "motivation.txt": letter_text("Lise Berg"),
        },
    )
    assert grouped(tmp_path) == {
        "Kari Nordmann": {
            "Cover letter - Kari Nordmann.txt": DocType.COVER_LETTER,
            "kari_nordmann_cv.txt": DocType.CV,
        },
        "Lise Berg": {"motivation.txt": DocType.COVER_LETTER},
        "Ola Hansen": {"CV_final.txt": DocType.CV, "application.txt": DocType.COVER_LETTER},
        "Per Olsen": {"document1.txt": DocType.COVER_LETTER, "resume (3).txt": DocType.CV},
    }
    lise = next(s for s in discover_folder(tmp_path) if s.label == "Lise Berg")
    assert lise.warnings == ["No CV found for this candidate."]


def test_cvs_and_letters_in_separate_folders(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "CVs/a.txt": cv_text("Kari Nordmann", "kari@example.no"),
            "CVs/b.txt": cv_text("Ola Hansen", "ola@example.com"),
            "Letters/1.txt": letter_text("Ola Hansen"),
            "Letters/2.txt": letter_text("Kari Nordmann"),
        },
    )
    assert grouped(tmp_path) == {
        "Kari Nordmann": {"CVs/a.txt": DocType.CV, "Letters/2.txt": DocType.COVER_LETTER},
        "Ola Hansen": {"CVs/b.txt": DocType.CV, "Letters/1.txt": DocType.COVER_LETTER},
    }


def test_folder_per_candidate_uses_content_for_types(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            # neither file name says what the document is
            "applicant-17/doc1.txt": letter_text("Kari Nordmann"),
            "applicant-17/doc2.txt": cv_text("Kari Nordmann", "kari@example.no"),
            "Ola Hansen/scan.txt": cv_text("Ola Hansen", "ola@example.com"),
        },
    )
    assert grouped(tmp_path) == {
        # a folder that is not named after a person is labelled with the detected name
        "Kari Nordmann": {
            "applicant-17/doc1.txt": DocType.COVER_LETTER,
            "applicant-17/doc2.txt": DocType.CV,
        },
        "Ola Hansen": {"Ola Hansen/scan.txt": DocType.CV},
    }


def test_letter_named_as_cv_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, {"Kari/cv.txt": letter_text("Kari Nordmann")})
    [source] = discover_folder(tmp_path)
    [f] = source.files
    assert f.doc_type is DocType.COVER_LETTER
    assert f.warnings == ["Named as a CV but reads like a cover letter."]


def test_overrides_and_exclusions(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "kari_nordmann_cv.txt": cv_text("Kari Nordmann", "kari@example.no"),
            "kari_nordmann_notes.txt": "Interview notes: strong on forecasting.",
            "ola_hansen_cv.txt": cv_text("Ola Hansen", "ola@example.com"),
        },
    )
    sources = discover_folder(
        tmp_path,
        overrides={"kari_nordmann_cv.txt": DocType.COVER_LETTER},
        exclude=["ola_hansen_cv.txt"],
    )
    assert [
        (s.label, [(f.display_path, f.doc_type, f.type_reason) for f in s.files]) for s in sources
    ] == [
        (
            "Kari Nordmann",
            [
                ("kari_nordmann_cv.txt", DocType.COVER_LETTER, "set by user"),
                ("kari_nordmann_notes.txt", DocType.OTHER, "file name"),
            ],
        )
    ]


def test_ingest_collects_names_for_redaction(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "CV_final.txt": cv_text("OLA HANSEN", "ola.h@example.com"),
            "application.txt": letter_text("Ola M. Hansen"),
        },
    )
    result = ingest(tmp_path)
    [cand] = result.candidates
    assert result.identity[cand.candidate_id] == "Ola Hansen"
    assert set(result.names[cand.candidate_id]) == {"Ola Hansen", "Ola M. Hansen"}


def test_identical_copies_are_skipped(tmp_path: Path) -> None:
    text = cv_text("Kari Nordmann", "kari@example.no")
    write(tmp_path, {"kari_nordmann_cv.txt": text, "kari_nordmann_cv (1).txt": text})
    [source] = discover_folder(tmp_path)
    assert [f.display_path for f in source.files] == ["kari_nordmann_cv (1).txt"]
    assert source.warnings == [
        "kari_nordmann_cv.txt is a copy of kari_nordmann_cv (1).txt; skipped."
    ]
