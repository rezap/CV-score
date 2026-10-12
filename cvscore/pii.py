"""Shared patterns for spotting personal contact details in text."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PROFILE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:linkedin\.com|github\.com|xing\.com)/[^\s\"'<>)]+", re.I
)
# Candidate phone numbers: digits with optional +, spaces, dots, dashes, parentheses between.
# Filtered by ``is_phone`` so date ranges like "2016 - 2019" are not mistaken for phones.
PHONE_CANDIDATE_RE = re.compile(r"(?<![\w/])\+?\(?\d(?:[\s().-]*\d){7,}(?![\w/])")
_YEAR_RE = re.compile(r"(?:19|20)\d\d")


def is_phone(candidate: str) -> bool:
    digits = re.sub(r"\D", "", candidate)
    if not 9 <= len(digits) <= 15:
        return False
    groups = re.findall(r"\d+", candidate)
    # "2016 - 2019 - 2021", "2019-2024": only years, not a phone number.
    return not all(_YEAR_RE.fullmatch(g) for g in groups)


def find_phones(text: str) -> list[re.Match[str]]:
    return [m for m in PHONE_CANDIDATE_RE.finditer(text) if is_phone(m.group())]


def has_contact_details(text: str) -> bool:
    return bool(
        EMAIL_RE.search(text)
        or PROFILE_URL_RE.search(text)
        or re.search(r"https?://|www\.", text, re.I)
        or find_phones(text)
    )
