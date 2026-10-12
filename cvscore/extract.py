"""Candidate documents -> structured ``CandidateProfile``.

One structured-output call on the redacted text, then deterministic post-processing:
dates parsed, quotes verified against the source (unverifiable ones dropped), and total
years of experience computed with overlapping roles counted once.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from cvscore.config import ModelRole
from cvscore.evidence import verify
from cvscore.llm.structured import LLMProvider
from cvscore.models import (
    Achievement,
    CandidateProfile,
    DocType,
    Education,
    Evidence,
    ExperienceEntry,
)
from cvscore.prompts import CANDIDATE_PROFILE_SYSTEM

DocName = Literal["cv", "cover_letter", "application_form", "other"]

# --- Model-facing schema (strict: all fields required, no defaults) -------------------


class _Experience(BaseModel):
    title: str
    organisation: str | None
    start: str | None
    end: str | None
    is_current: bool
    seniority: str | None
    domain: str | None
    responsibilities: list[str]
    technologies: list[str]
    evidence_doc: DocName
    evidence_quote: str


class _Achievement(BaseModel):
    statement: str
    metric: str | None
    experience_index: int | None
    evidence_doc: DocName
    evidence_quote: str


class _Education(BaseModel):
    degree: str
    field: str | None
    institution: str | None
    end: str | None


class _TraitEvidence(BaseModel):
    label: str
    evidence_doc: DocName
    quote: str


class CandidateProfileDraft(BaseModel):
    experience: list[_Experience]
    achievements: list[_Achievement]
    skills: list[str]
    education: list[_Education]
    certifications: list[str]
    languages: list[str]
    trait_evidence: list[_TraitEvidence]


_MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("jan", "january", "januar", "jänner"),
            ("feb", "february", "februar"),
            ("mar", "march", "mär", "märz", "maerz"),
            ("apr", "april"),
            ("may", "mai"),
            ("jun", "june", "juni"),
            ("jul", "july", "juli"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october", "okt", "oktober"),
            ("nov", "november"),
            ("dec", "december", "dez", "dezember"),
        ],
        start=1,
    )
    for name in names
}


def parse_date(value: str | None) -> date | None:
    """'2021-03', '2021', '03/2021', 'Mar 2021', 'März 2021' -> first day of that month."""
    if not value:
        return None
    text = value.lower()
    year: int | None = None
    month = 1
    if m := re.search(r"(?<!\d)(\d{1,2})[/.](\d{4})(?!\d)", text):  # 03/2021
        month, year = int(m.group(1)), int(m.group(2))
    elif m := re.search(r"(?<!\d)(\d{4})(?:[-/.](\d{1,2}))?(?!\d)", text):  # 2021 or 2021-03
        year = int(m.group(1))
        if m.group(2):
            month = int(m.group(2))
        elif name := re.search(r"[a-zäöü]+", text):
            month = _MONTHS.get(name.group(), 1)
    if year is None or not 1900 <= year <= 2100 or not 1 <= month <= 12:
        return None
    return date(year, month, 1)


def years_of_experience(entries: list[ExperienceEntry], today: date | None = None) -> float | None:
    """Total years across dated roles, counting overlapping periods once."""
    today = today or date.today()
    spans = []
    for e in entries:
        if e.start is None:
            continue
        end = e.end or (today if e.is_current else None)
        if end is None or end < e.start:
            continue
        spans.append((e.start, end))
    if not spans:
        return None
    spans.sort()
    total_days = 0
    cur_start, cur_end = spans[0]
    for span_start, span_end in spans[1:]:
        if span_start <= cur_end:
            cur_end = max(cur_end, span_end)
        else:
            total_days += (cur_end - cur_start).days
            cur_start, cur_end = span_start, span_end
    total_days += (cur_end - cur_start).days
    return round(total_days / 365.25, 1)


def documents_prompt(docs: dict[DocType, str]) -> str:
    parts = [
        f'<document type="{doc_type}">\n{text}\n</document>' for doc_type, text in docs.items()
    ]
    return "\n\n".join(parts)


def extract_profile(
    candidate_id: str,
    docs: dict[DocType, str],
    llm: LLMProvider,
    config: RunnableConfig | None = None,
) -> CandidateProfile:
    """``docs`` is redacted text per document type (see ``cvscore.redact``)."""
    draft = llm(ModelRole.EXTRACTOR).generate(
        CandidateProfileDraft,
        [SystemMessage(CANDIDATE_PROFILE_SYSTEM), HumanMessage(documents_prompt(docs))],
        config,
    )
    dropped = 0

    def check(doc: str, quote: str) -> list[Evidence]:
        nonlocal dropped
        kept, n = verify([(doc, quote)], docs)
        dropped += n
        return kept

    experience = [
        ExperienceEntry(
            title=e.title,
            organisation=e.organisation,
            start=parse_date(e.start),
            end=None if e.is_current else parse_date(e.end),
            is_current=e.is_current,
            seniority=e.seniority,
            domain=e.domain,
            responsibilities=e.responsibilities,
            technologies=e.technologies,
            evidence=check(e.evidence_doc, e.evidence_quote),
        )
        for e in draft.experience
    ]
    achievements = [
        Achievement(
            statement=a.statement,
            metric=a.metric,
            experience_index=(
                a.experience_index
                if a.experience_index is not None and 0 <= a.experience_index < len(experience)
                else None
            ),
            evidence=check(a.evidence_doc, a.evidence_quote),
        )
        for a in draft.achievements
    ]
    # An achievement nobody can find in the documents is not an achievement.
    achievements = [a for a in achievements if a.evidence]
    trait_evidence: list[Evidence] = []
    for t in draft.trait_evidence:
        for ev in check(t.evidence_doc, t.quote):
            trait_evidence.append(ev.model_copy(update={"label": t.label}))

    return CandidateProfile(
        candidate_id=candidate_id,
        experience=experience,
        achievements=achievements,
        skills=draft.skills,
        education=[
            Education(
                degree=e.degree, field=e.field, institution=e.institution, end=parse_date(e.end)
            )
            for e in draft.education
        ],
        certifications=draft.certifications,
        languages=draft.languages,
        trait_evidence=trait_evidence,
        years_experience=years_of_experience(experience),
        dropped_quotes=dropped,
    )
