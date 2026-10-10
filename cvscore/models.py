"""Typed artefacts passed between pipeline stages.

Every stage reads and writes these models (serialised as JSON), so stages can be
cached, inspected and tested in isolation. LLM calls use them as structured-output
schemas, so field descriptions double as instructions to the model.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class DocType(StrEnum):
    CV = "cv"
    COVER_LETTER = "cover_letter"
    APPLICATION_FORM = "application_form"
    OTHER = "other"


class Evidence(BaseModel):
    """A verbatim quote from a candidate document that supports a claim or score."""

    doc_type: DocType
    quote: str = Field(description="Exact text copied from the document, not paraphrased.")
    location: str | None = Field(default=None, description="Page, section or form question.")


# --- Job ---------------------------------------------------------------------


class Requirement(BaseModel):
    id: str
    description: str
    weight: float = Field(default=1.0, ge=0)


class Trait(BaseModel):
    id: str
    name: str
    definition: str = Field(description="What this trait means for this role.")
    evidence_examples: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0)


class Dimension(StrEnum):
    REQUIREMENTS = "requirements"
    EXPERIENCE = "experience"
    SKILLS = "skills"
    ACHIEVEMENTS = "achievements"
    TRAITS = "traits"


DEFAULT_WEIGHTS: dict[Dimension, float] = {
    Dimension.EXPERIENCE: 0.35,
    Dimension.ACHIEVEMENTS: 0.25,
    Dimension.SKILLS: 0.25,
    Dimension.REQUIREMENTS: 0.15,
    Dimension.TRAITS: 0.0,
}


class JobProfile(BaseModel):
    id: str
    title: str
    seniority: str | None = None
    domain: str | None = None
    summary: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    must_have: list[Requirement] = Field(default_factory=list)
    nice_to_have: list[Requirement] = Field(default_factory=list)
    key_skills: list[str] = Field(default_factory=list)
    traits: list[Trait] = Field(default_factory=list)
    weights: dict[Dimension, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


# --- Candidate ---------------------------------------------------------------


class SourceDocument(BaseModel):
    doc_type: DocType
    path: str
    text: str


class CandidateDocuments(BaseModel):
    candidate_id: str = Field(description="Pseudonymous ID; never the candidate's name.")
    documents: list[SourceDocument] = Field(default_factory=list)


class ExperienceEntry(BaseModel):
    title: str
    organisation: str | None = None
    start: date | None = None
    end: date | None = Field(default=None, description="None if this is the current role.")
    seniority: str | None = None
    domain: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class Achievement(BaseModel):
    statement: str
    metric: str | None = Field(default=None, description="Quantified result, if stated.")
    scope: str | None = None
    experience_index: int | None = Field(
        default=None, description="Index into CandidateProfile.experience, if linked."
    )
    evidence: list[Evidence] = Field(default_factory=list)


class Education(BaseModel):
    degree: str
    field: str | None = None
    institution: str | None = None
    end: date | None = None


class CandidateProfile(BaseModel):
    candidate_id: str
    experience: list[ExperienceEntry] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    trait_evidence: list[Evidence] = Field(default_factory=list)


# --- Scoring -----------------------------------------------------------------


class RequirementStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNCLEAR = "unclear"


class RequirementCheck(BaseModel):
    requirement_id: str
    status: RequirementStatus
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)


class DimensionScore(BaseModel):
    dimension: Dimension
    score: float | None = Field(
        ge=0, le=1, description="None means insufficient evidence (treated as neutral)."
    )
    confidence: float = Field(ge=0, le=1)
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)


class Flag(StrEnum):
    KNOCKOUT_FAILED = "knockout_failed"
    LOW_CONFIDENCE = "low_confidence"
    INCONSISTENCY = "inconsistency"
    NEEDS_REVIEW = "needs_review"


class ScoreCard(BaseModel):
    candidate_id: str
    job_id: str
    requirement_checks: list[RequirementCheck] = Field(default_factory=list)
    scores: list[DimensionScore] = Field(default_factory=list)
    total: float | None = None
    flags: list[Flag] = Field(default_factory=list)
    trace_id: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
