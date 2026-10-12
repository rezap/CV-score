"""Job ad -> ``JobProfile``, saved as YAML for a human to review and edit before scoring."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from cvscore.config import ModelRole
from cvscore.llm.structured import LLMProvider
from cvscore.models import DEFAULT_WEIGHTS, JobProfile, Requirement, Trait
from cvscore.prompts import JOB_PROFILE_SYSTEM

# --- Model-facing schema (strict: all fields required, no defaults) -------------------


class _MustHave(BaseModel):
    description: str


class _NiceToHave(BaseModel):
    description: str
    importance: Literal["low", "medium", "high"]


class _Trait(BaseModel):
    name: str
    definition: str
    evidence_examples: list[str]


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


_IMPORTANCE_WEIGHT = {"low": 0.5, "medium": 1.0, "high": 2.0}


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
            Requirement(id=f"M{i}", description=m.description)
            for i, m in enumerate(draft.must_have, 1)
        ],
        nice_to_have=[
            Requirement(
                id=f"N{i}", description=n.description, weight=_IMPORTANCE_WEIGHT[n.importance]
            )
            for i, n in enumerate(draft.nice_to_have, 1)
        ],
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
    )


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
