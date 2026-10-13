"""Shared patterns for spotting personal contact details in text."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PROFILE_URL_RE = re.compile(
    r"(?<![\w.])(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|xing\.com)/"
    r"[^\s\"'<>)]*[^\s\"'<>).,;:]",
    re.I,
)
# Candidate phone numbers: digits with optional +, spaces, dots, dashes, parentheses between.
# Filtered by ``is_phone`` so dates like "2016 - 2019" or "01.2019 - 05.2021" are kept.
PHONE_CANDIDATE_RE = re.compile(r"(?<![\w/])\+?\(?\d(?:[\s().-]*\d){7,}(?![\w/])")
_YEAR_RE = re.compile(r"(?:18|19|20)\d\d")
REDACTED_RE = re.compile(r"\[REDACTED(?:: [^\]]+)?\]")


def redacted(what: str) -> str:
    """The marker that replaces removed personal data, e.g. ``[REDACTED: email]``."""
    return f"[REDACTED: {what}]"


def is_phone(candidate: str) -> bool:
    """8-15 digits (Norwegian and Danish numbers have 8) that do not read as a date."""
    digits = re.sub(r"\D", "", candidate)
    if not 8 <= len(digits) <= 15:
        return False
    groups = re.findall(r"\d+", candidate)
    # A year plus only 1-2 digit groups (months, days) is a date or a date range:
    # "2016 - 2019", "01.2019 - 05.2021", "01.01.2020".
    has_year = any(_YEAR_RE.fullmatch(g) for g in groups)
    return not (has_year and all(_YEAR_RE.fullmatch(g) or len(g) <= 2 for g in groups))


def find_phones(text: str) -> list[re.Match[str]]:
    return [m for m in PHONE_CANDIDATE_RE.finditer(text) if is_phone(m.group())]


def has_contact_details(text: str) -> bool:
    return bool(
        EMAIL_RE.search(text)
        or PROFILE_URL_RE.search(text)
        or re.search(r"https?://|www\.", text, re.I)
        or find_phones(text)
    )
