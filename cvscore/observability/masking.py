"""Masks personal data before it is sent to Langfuse.

This is a safety net for traces, not the redaction step that runs before scoring:
it catches the identifiers that are easy to spot reliably (emails, phone numbers,
URLs to personal profiles) wherever they appear in a trace payload.
"""

from __future__ import annotations

from typing import Any

from cvscore.pii import EMAIL_RE, PHONE_CANDIDATE_RE, PROFILE_URL_RE, is_phone, redacted


def mask_text(text: str) -> str:
    text = EMAIL_RE.sub(redacted("email"), text)
    text = PROFILE_URL_RE.sub(redacted("profile URL"), text)
    return PHONE_CANDIDATE_RE.sub(
        lambda m: redacted("phone") if is_phone(m.group()) else m.group(), text
    )


def mask_pii(*, data: Any, **_: Any) -> Any:
    """Langfuse ``mask`` hook: recursively masks strings in a trace payload."""
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        return {k: mask_pii(data=v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return type(data)(mask_pii(data=v) for v in data)
    return data
