"""Where candidates live, from their documents (fictional people and addresses)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from cvscore.config import LocationConfig, load_settings
from cvscore.ingest.plaintext import extract_text_file
from cvscore.location import locate
from cvscore.models import DocType, SourceDocument


def doc(tmp_path: Path, text: str, doc_type: DocType = DocType.CV) -> SourceDocument:
    path = tmp_path / f"{doc_type.value}-{abs(hash(text))}.txt"
    path.write_text(text, encoding="utf-8")
    return extract_text_file(path, doc_type=doc_type, display_path=path.name)


EXPERIENCE = """
Experience
Data Scientist, Example Analytics AS, 2019 - present
Built forecasting models in Python and R for retail clients.

Education
MSc Statistics, University of Example, 2018
"""


@pytest.mark.parametrize(
    ("header", "region", "country"),
    [
        ("Kari Nordmann\nStorgata 1, 0155 Oslo\nkari@example.com", "home", "NO"),
        ("Jan Kowalski\nWarsaw, Poland\njan@example.com", "eu", "PL"),
        ("Tom Example\nManchester, UK | tom@example.com", "europe", "GB"),
        ("Priya Example\nBengaluru, India\npriya@example.com", "world", "IN"),
        ("Ola Example\nAddress: Kongens gate 2, Trondheim", "home", "NO"),
        ("Lena Example\nN-5003\nlena@example.com", "home", "NO"),
    ],
)
def test_location_from_the_cv_header(
    tmp_path: Path, header: str, region: str, country: str
) -> None:
    loc = locate([doc(tmp_path, header + "\n" + EXPERIENCE)])
    assert (loc.region, loc.country, loc.confident) == (region, country, True)


@pytest.mark.parametrize(
    ("contact", "country"),
    [("mia@example.com | +46 70 123 45 67", "SE"), ("ali@example.com | 0092 300 1234567", "PK")],
)
def test_phone_country_code_is_a_guess(tmp_path: Path, contact: str, country: str) -> None:
    loc = locate([doc(tmp_path, "Sam Example\n" + contact + "\n" + EXPERIENCE)])
    # People keep their number when they move, so a phone alone is not confident.
    assert (loc.country, loc.source, loc.confident) == (country, "phone", False)


def test_address_beats_phone_and_old_jobs(tmp_path: Path) -> None:
    cv = doc(
        tmp_path,
        "Sam Example\nBergen, Norway · +49 151 2345 6789 · sam@example.com\n\n"
        "Experience\nEngineer, Example GmbH, Berlin, 2015 - 2020\n",
    )
    loc = locate([cv])
    assert loc.country == "NO" and loc.source == "address"


def test_cover_letter_sentence_and_relocation(tmp_path: Path) -> None:
    cv = doc(tmp_path, "Experience\nAnalyst, Example Ltd, 2020 - present\n")
    letter = doc(
        tmp_path,
        "Dear hiring manager,\n\nI currently live in Lisbon and am keen to relocate to "
        "Norway for this role.\n\nKind regards,\nAna Example",
        DocType.COVER_LETTER,
    )
    loc = locate([cv, letter])
    assert (loc.country, loc.region, loc.source, loc.relocation) == ("PT", "eu", "letter", True)


def test_employer_address_in_a_letter_does_not_count(tmp_path: Path) -> None:
    letter = doc(
        tmp_path,
        "Example Energy ASA\nForusbeen 50, 4035 Stavanger\n\nDear hiring manager,\n"
        "I am applying for the data scientist role.\n",
        DocType.COVER_LETTER,
    )
    assert locate([letter]).region is None


def test_weak_guess_from_roles_skips_education(tmp_path: Path) -> None:
    cv = doc(
        tmp_path,
        "Profile\nCurious analyst.\n\nExperience\nAnalyst, Example Bank, Dublin, 2021 - now\n"
        "\nEducation\nBSc, University of Toronto, 2020\n",
    )
    loc = locate([cv])
    assert (loc.country, loc.confident) == ("IE", False)

    only_school = doc(tmp_path, "Profile\nAnalyst.\n\nEducation\nBSc, Toronto, 2020\n")
    assert locate([only_school]).region is None


def test_a_name_is_not_a_place(tmp_path: Path) -> None:
    cv = doc(tmp_path, "Paris Example\nparis@example.com\n" + EXPERIENCE)
    assert locate([cv], names=["Paris Example"]).region is None
    assert locate([cv]).country == "FR"  # without the name it would be read as a city


def test_dates_are_not_phone_numbers(tmp_path: Path) -> None:
    cv = doc(tmp_path, "Sam Example\n01.2019 - 05.2021\n" + EXPERIENCE)
    assert locate([cv]).region is None


def test_home_country_is_configurable(tmp_path: Path) -> None:
    berlin = doc(tmp_path, "Max Example\nBerlin, Germany\nmax@example.com\n" + EXPERIENCE)
    oslo = doc(tmp_path, "Kari Example\nOslo, Norway\nkari@example.com\n" + EXPERIENCE)
    assert locate([berlin], home="DE").region == "home"
    # Away from home, Norway is Europe outside the EU.
    assert (locate([oslo], home="DE").region, locate([oslo]).region) == ("europe", "home")

    letter = doc(
        tmp_path,
        "Dear hiring manager,\n\nI would love to move to Munich for this role.\n",
        DocType.COVER_LETTER,
    )
    assert locate([letter], home="DE").relocation


@pytest.mark.parametrize(
    ("value", "code"), [("NO", "NO"), ("de", "DE"), ("Norway", "NO"), ("Deutschland", "DE")]
)
def test_home_country_by_code_or_name(value: str, code: str) -> None:
    assert LocationConfig(home_country=value).home_country == code


def test_unknown_home_country_is_an_error() -> None:
    with pytest.raises(ValidationError, match="Unknown country"):
        LocationConfig(home_country="Atlantis")


def test_unquoted_no_in_yaml_means_norway(tmp_path: Path) -> None:
    config = tmp_path / "cvscore.yaml"
    config.write_text("location:\n  home_country: NO\n")  # YAML reads NO as false
    assert load_settings(config).location.home_country == "NO"
