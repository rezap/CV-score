"""Masks personal data before it is sent to Langfuse.

This is a safety net for traces, not the redaction step that runs before scoring:
it catches the identifiers that are easy to spot reliably (emails, phone numbers,
URLs to personal profiles) wherever they appear in a trace payload.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (
        re.compile(
            r"https?://(?:www\.)?(?:linkedin\.com|github\.com|xing\.com)/[^\s\"'<>)]+", re.I
        ),
        "[PROFILE_URL]",
    ),
    # International or local phone numbers: 8+ digits, optional +, spaces, dots,
    # dashes and parentheses between them.
    (re.compile(r"(?<![\w/])\+?\d(?:[\s().-]*\d){7,}(?![\w/])"), "[PHONE]"),
]


def mask_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def mask_pii(*, data: Any, **_: Any) -> Any:
    """Langfuse ``mask`` hook: recursively masks strings in a trace payload."""
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        return {k: mask_pii(data=v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return type(data)(mask_pii(data=v) for v in data)
    return data
