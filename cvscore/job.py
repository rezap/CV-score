"""Job ad -> ``JobProfile``, saved as YAML for a human to review and edit before scoring."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, TypeVar

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from cvscore.config import ModelRole
from cvscore.llm.structured import LLMProvider
from cvscore.models import (
    DEFAULT_WEIGHTS,
    EvidenceLevel,
    JobProfile,
    MatchMode,
    Requirement,
    ScreeningCriterion,
    Trait,
)
from cvscore.prompts import JOB_PROFILE_SYSTEM

# --- Model-facing schema (strict: all fields required, no defaults) -------------------


Evidence = Literal["mentioned", "applied", "proven"]
Match = Literal["exact", "equivalent"]


class _MustHave(BaseModel):
    description: str
    evidence: Evidence
    match: Match


class _NiceToHave(BaseModel):
    description: str
    importance: Literal["low", "medium", "high"]
    evidence: Evidence
    match: Match


class _Trait(BaseModel):
    name: str
    definition: str
    evidence_examples: list[str]


class _Screening(BaseModel):
    name: str
    terms: list[str]
    required: bool
    importance: Literal["low", "medium", "high"]


class JobProfileDraft(BaseModel):
    title: str
    seniority: str | None
    domain: str | None
    summary: str
    responsibilities: list[str]
    must_have: list[_MustHave]
    nice_to_have: list[_NiceToHave]
    key_skills: list[str]
    traits: list[_Trait]
    screening: list[_Screening]
    target_years: float | None


_IMPORTANCE_WEIGHT = {"low": 0.5, "medium": 1.0, "high": 2.0}
_T = TypeVar("_T")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "job"


def from_draft(draft: JobProfileDraft) -> JobProfile:
    return JobProfile(
        id=slugify(draft.title),
        title=draft.title,
        seniority=draft.seniority,
        domain=draft.domain,
        summary=draft.summary,
        responsibilities=draft.responsibilities,
        must_have=[
            Requirement(
                id=f"M{i}",
                description=m.description,
                evidence=_unless_default(EvidenceLevel(m.evidence), EvidenceLevel.APPLIED),
                match=_unless_default(MatchMode(m.match), MatchMode.EQUIVALENT),
            )
            for i, m in enumerate(draft.must_have, 1)
        ],
        nice_to_have=[
            Requirement(
                id=f"N{i}",
                description=n.description,
                weight=_IMPORTANCE_WEIGHT[n.importance],
                evidence=_unless_default(EvidenceLevel(n.evidence), EvidenceLevel.APPLIED),
                match=_unless_default(MatchMode(n.match), MatchMode.EQUIVALENT),
            )
            for i, n in enumerate(draft.nice_to_have, 1)
        ],
        target_years=draft.target_years,
        key_skills=draft.key_skills,
        traits=[
            Trait(
                id=f"T{i}",
                name=t.name,
                definition=t.definition,
                evidence_examples=t.evidence_examples,
                # Shown but not scored by default; raise the weight to let it count.
                weight=1.0,
            )
            for i, t in enumerate(draft.traits, 1)
        ],
        weights=dict(DEFAULT_WEIGHTS),
        screening=[
            ScreeningCriterion(
                id=f"S{i}",
                name=c.name,
                terms=_unique(c.terms),
                required=c.required,
                weight=_IMPORTANCE_WEIGHT[c.importance],
            )
            for i, c in enumerate(draft.screening, 1)
            if c.terms
        ]
        or screening_from_skills(draft.key_skills),
    )


def _unless_default(value: _T, default: _T) -> _T | None:
    """Store an item's setting only when it differs from the job's default, so changing
    the default in the editor changes every item that has not been set on its own."""
    return None if value == default else value


def _unique(terms: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for t in terms:
        t = t.strip()
        if t and t.lower() not in seen:
            seen[t.lower()] = t
    return list(seen.values())


def screening_from_skills(key_skills: list[str]) -> list[ScreeningCriterion]:
    """Fallback screening (e.g. for a job.yaml written before screening existed): one
    criterion per key skill, none required."""
    return [
        ScreeningCriterion(id=f"S{i}", name=skill, terms=[skill])
        for i, skill in enumerate(key_skills, 1)
        if skill.strip()
    ]


def draft_job_profile(
    ad_text: str, llm: LLMProvider, config: RunnableConfig | None = None
) -> JobProfile:
    draft = llm(ModelRole.AGENT).generate(
        JobProfileDraft,
        [SystemMessage(JOB_PROFILE_SYSTEM), HumanMessage(f"Job advertisement:\n\n{ad_text}")],
        config,
    )
    return from_draft(draft)


_YAML_HEADER = """\
# Hiring profile used to assess every candidate. Review and edit before running.
# - must_have: knock-out requirements (a clear "fail" flags the candidate).
# - nice_to_have / traits: weight = relative importance within the list.
# - evidence (per requirement, or evidence_default): how it must appear to count in full:
#   mentioned (listed anywhere), applied (used in a described role or project), proven
#   (applied with ownership, scale or results). match: exact, or equivalent (comparable
#   alternatives count).
# - weights: share of the total score per dimension. traits is 0 by default, so traits are
#   assessed and shown but do not change the ranking; raise it to let them count.
"""


def save_job_profile(profile: JobProfile, path: Path) -> None:
    data = profile.model_dump(mode="json")
    path.write_text(
        _YAML_HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


def load_job_profile(path: Path) -> JobProfile:
    return JobProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
