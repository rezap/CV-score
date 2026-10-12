"""Removes identifying details from candidate documents before any model sees them.

- Blocks flagged as contact details during ingestion (name banner, header/footer, contact
  lines, personal-data form answers) are dropped.
- Lines stating protected or identifying attributes (date of birth, nationality, marital
  status, ...) are dropped.
- Emails, phone numbers and profile URLs are masked anywhere in the text.
- The candidate's name (from the folder/manifest label) is replaced by ``[CANDIDATE]``.

Redaction is a best-effort safety measure, not a guarantee: free text can still reveal
identity (e.g. a photo caption or a gendered pronoun in a reference).
"""

from __future__ import annotations

import re

from cvscore.ingest.document import render_markdown
from cvscore.models import CandidateDocuments, DocType
from cvscore.observability.masking import mask_text

_PERSONAL_LINE = re.compile(
    r"^\s*(date of birth|birth ?date|born|dob|age|gender|sex|nationality|citizenship|"
    r"marital status|family status|religion|place of birth|geburtsdatum|geboren|"
    r"staatsangehörigkeit|nationalität|familienstand|geschlecht|religion|alter)\b",
    re.I,
)


def _name_pattern(label: str | None) -> re.Pattern[str] | None:
    if not label:
        return None
    parts = [p for p in re.split(r"[\s_\-.,]+", label) if len(p) >= 2 and not p.isdigit()]
    if not parts:
        return None
    alternatives = "|".join(re.escape(p) for p in sorted(parts, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternatives})\b", re.I)


def redact(cand: CandidateDocuments, name_label: str | None = None) -> dict[DocType, str]:
    """Redacted markdown text per document type (documents of the same type are joined)."""
    name_re = _name_pattern(name_label)
    out: dict[DocType, list[str]] = {}
    for doc in cand.documents:
        blocks = [
            b.model_copy()
            for b in doc.blocks
            if not b.is_contact and not _PERSONAL_LINE.match(b.text)
        ]
        for b in blocks:
            b.text = mask_text(b.text)
            if name_re is not None:
                b.text = name_re.sub("[CANDIDATE]", b.text)
        text = render_markdown(blocks)
        if text.strip():
            out.setdefault(doc.doc_type, []).append(text)
    return {doc_type: "\n\n".join(texts) for doc_type, texts in out.items()}
