"""Checks that quotes returned by a model really appear in the source documents."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from cvscore.models import DocType, Evidence

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")
_QUOTES = dict.fromkeys(map(ord, "\u2018\u2019\u201a\u201b\u2032"), "'") | dict.fromkeys(
    map(ord, "\u201c\u201d\u201e\u201f\u2033"), '"'
)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES).lower()
    return re.sub(r"\s+", " ", text).strip()


def _alnum(text: str) -> str:
    return re.sub(r"[^\w]+", "", _normalize(text))


@lru_cache(maxsize=128)
def _document_forms(text: str) -> tuple[str, str]:
    """A document normalised both ways, once: every quote is checked against it."""
    normalized = _normalize(text)
    return normalized, re.sub(r"[^\w]+", "", normalized)


MIN_QUOTE_CHARS = 12


def quote_in(quote: str, text: str) -> bool:
    """True if ``quote`` appears in ``text``, ignoring case, whitespace, dash/quote styles,
    markdown markers and punctuation (models often drop a bullet or join two lines)."""
    q = _normalize(quote).strip(" .\"'")
    if len(q) < MIN_QUOTE_CHARS:
        return False
    normalized, alnum = _document_forms(text)
    if q in normalized:
        return True
    aq = _alnum(quote)
    return len(aq) >= MIN_QUOTE_CHARS and aq in alnum


def verify(quotes: list[tuple[str, str]], docs: dict[DocType, str]) -> tuple[list[Evidence], int]:
    """Keep quotes found in their document (or, failing that, any document).

    ``quotes`` are (doc_type, quote) pairs as the model returned them. Returns the verified
    evidence and how many quotes were dropped.
    """
    kept: list[Evidence] = []
    dropped = 0
    for doc_name, quote in quotes:
        claimed = DocType(doc_name) if doc_name in DocType._value2member_map_ else None
        order = [claimed] if claimed in docs else []
        order += [d for d in docs if d is not claimed]
        found = next((d for d in order if d is not None and quote_in(quote, docs[d])), None)
        if found is None:
            dropped += 1
        else:
            kept.append(Evidence(doc_type=found, quote=quote.strip()))
    return kept, dropped
