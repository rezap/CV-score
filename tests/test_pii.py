import pytest

from cvscore.pii import find_phones, has_contact_details, is_phone


@pytest.mark.parametrize(
    ("text", "phone"),
    [
        ("+44 20 7946 0958", True),
        ("+49 (0)30 1234-5678", True),
        ("(555) 123-4567", True),
        ("020 7946 0958", True),
        ("2016 - 2019", False),
        ("2019-2024", False),
        ("2016 - 2019 - 2021", False),
        ("12 345", False),
    ],
)
def test_is_phone(text: str, phone: bool) -> None:
    assert is_phone(text) is phone


def test_find_phones_and_contact_details() -> None:
    text = "Acme Ltd, 2016 - 2019. Call +44 20 7946 0958."
    assert [m.group() for m in find_phones(text)] == ["+44 20 7946 0958"]
    assert has_contact_details("jane@example.com")
    assert has_contact_details("linkedin.com/in/x and www.example.org")
    assert not has_contact_details("Senior Engineer, Acme Ltd, 2016 - 2019")
