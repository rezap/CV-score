"""Removes identifying details from candidate documents before any model sees them.

Everything removed is replaced by a visible marker, so a reader (and the model) can see
that something was there:

- Blocks flagged as contact details during ingestion (name banner, header/footer, contact
  lines, personal-data form answers) become ``[REDACTED: contact details]``; consecutive
  ones collapse into one marker.
- Lines stating protected or identifying attributes (date of birth, nationality, marital
  status, ...) become ``[REDACTED: personal details]``.
- Emails, phone numbers and profile URLs become ``[REDACTED: email]`` etc. anywhere.
- The candidate's name (detected in the documents, or the folder/manifest label) becomes
  ``[REDACTED: name]``.

Redaction is a best-effort safety measure, not a guarantee: free text can still reveal
identity (e.g. a photo caption or a gendered pronoun in a reference).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from cvscore.ingest.document import render_markdown
from cvscore.models import BlockKind, CandidateDocuments, DocType, TextBlock
from cvscore.observability.masking import mask_text
from cvscore.pii import REDACTED_RE, redacted

_PERSONAL_LINE = re.compile(
    r"^\s*(date of birth|birth ?date|born|dob|age|gender|sex|nationality|citizenship|"
    r"marital status|family status|religion|place of birth|geburtsdatum|geboren|"
    r"staatsangehörigkeit|nationalität|familienstand|geschlecht|religion|alter|"
    r"fødselsdato|født|kjønn|nasjonalitet|statsborgerskap|sivilstand|sivilstatus|"
    r"födelsedatum|nationalitet|civilstånd)\b",
    re.I,
)
_NAME_MARKER = redacted("name")


def name_pattern(names: Iterable[str | None]) -> re.Pattern[str] | None:
    """Matches any part (2+ letters) of any of ``names`` as a whole word, capitalised or in
    capitals ("Mark", "MARK"), so a name that is also a word ("mark") stays in the text."""
    parts = {
        form
        for name in names
        if name
        for p in re.split(r"[\s_\-.,]+", name)
        if len(p) >= 2 and not p.isdigit()
        for form in (p[0].upper() + p[1:].lower(), p.upper(), p)
    }
    if not parts:
        return None
    alternatives = "|".join(re.escape(p) for p in sorted(parts, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternatives})\b")


def _collapse_names(text: str) -> str:
    # "[REDACTED: name] [REDACTED: name]" (first and last name) reads as one name.
    return re.sub(
        rf"{re.escape(_NAME_MARKER)}(?:[\s.]+{re.escape(_NAME_MARKER)})+", _NAME_MARKER, text
    )


def redact_blocks(blocks: list[TextBlock], names: Iterable[str | None] = ()) -> list[TextBlock]:
    name_re = name_pattern(names)
    out: list[TextBlock] = []
    for b in blocks:
        if b.is_contact or _PERSONAL_LINE.match(b.text):
            marker = redacted("contact details" if b.is_contact else "personal details")
            if not (out and out[-1].text == marker):
                out.append(TextBlock(kind=BlockKind.PARAGRAPH, text=marker, section=b.section))
            continue
        b = b.model_copy()
        b.text = mask_text(b.text)
        if name_re is not None:
            b.text = _collapse_names(name_re.sub(_NAME_MARKER, b.text))
        out.append(b)
    return out


def redact(cand: CandidateDocuments, names: Iterable[str | None] = ()) -> dict[DocType, str]:
    """Redacted markdown text per document type (documents of the same type are joined).

    A document with nothing left but redaction markers is left out.
    """
    names = list(names)
    out: dict[DocType, list[str]] = {}
    for doc in cand.documents:
        text = render_markdown(redact_blocks(doc.blocks, names))
        if REDACTED_RE.sub("", text).strip():
            out.setdefault(doc.doc_type, []).append(text)
    return {doc_type: "\n\n".join(texts) for doc_type, texts in out.items()}
