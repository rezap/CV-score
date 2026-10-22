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
    label: str | None = Field(default=None, description="What the quote shows, e.g. a trait.")


# --- Job ---------------------------------------------------------------------


class EvidenceLevel(StrEnum):
    """How a requirement or skill must appear in the documents to count in full."""

    MENTIONED = "mentioned"  # listed anywhere (a skills section is enough)
    APPLIED = "applied"  # used in a described role, project or study
    PROVEN = "proven"  # applied with ownership, scale or results


class MatchMode(StrEnum):
    EXACT = "exact"  # only what is named counts (Terraform)
    EQUIVALENT = "equivalent"  # comparable alternatives count too (Pulumi, Bicep, ...)


LEVEL_RANK: dict[str, int] = {"none": 0, "mentioned": 1, "applied": 2, "proven": 3}


def level_value(observed: str, required: EvidenceLevel) -> float:
    """0..1 credit for evidence at ``observed`` level when ``required`` is asked for:
    full at or above it, a share below it (listed when use is required: 0.5)."""
    return min(1.0, LEVEL_RANK.get(observed, 0) / LEVEL_RANK[required.value])


class Requirement(BaseModel):
    id: str
    description: str
    weight: float = Field(default=1.0, ge=0)
    # None: the job's evidence_default / match_default.
    evidence: EvidenceLevel | None = None
    match: MatchMode | None = None


class Trait(BaseModel):
    id: str
    name: str
    definition: str = Field(description="What this trait means for this role.")
    evidence_examples: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0)


class ScreeningCriterion(BaseModel):
    """A cheap, model-free check: does the CV mention any of these terms?"""

    id: str
    name: str = Field(description="What is looked for, e.g. 'Programming for data analysis'.")
    terms: list[str] = Field(
        default_factory=list,
        description="Words that show it: synonyms, tools, abbreviations, other languages.",
    )
    required: bool = Field(
        default=False,
        description="A qualified CV must mention it; without any match the CV is screened out.",
    )
    weight: float = Field(default=1.0, ge=0)
    # None: the job's evidence_default. Under "applied" or "proven", terms found only in a
    # skills list or summary count half.
    evidence: EvidenceLevel | None = None


class Dimension(StrEnum):
    REQUIREMENTS = "requirements"
    EXPERIENCE = "experience"
    SKILLS = "skills"
    ACHIEVEMENTS = "achievements"
    TRAITS = "traits"


DEFAULT_WEIGHTS: dict[Dimension, float] = {
    Dimension.EXPERIENCE: 0.30,
    Dimension.REQUIREMENTS: 0.30,  # must-haves and nice-to-haves
    Dimension.SKILLS: 0.25,
    Dimension.ACHIEVEMENTS: 0.15,  # kept low: it rewards CVs that state numbers
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
    screening: list[ScreeningCriterion] = Field(default_factory=list)
    # How requirements, key skills and screening terms must appear, unless an item says
    # otherwise, and whether comparable alternatives count.
    evidence_default: EvidenceLevel = EvidenceLevel.APPLIED
    match_default: MatchMode = MatchMode.EQUIVALENT
    # Years of relevant experience the role calls for (from the ad, or by seniority).
    target_years: float | None = Field(default=None, ge=0)

    def evidence_for(self, item: Requirement | ScreeningCriterion) -> EvidenceLevel:
        return item.evidence or self.evidence_default

    def match_for(self, item: Requirement) -> MatchMode:
        return item.match or self.match_default


# --- Candidate ---------------------------------------------------------------


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE_ROW = "table_row"


class TextBlock(BaseModel):
    """One unit of extracted text, in reading order."""

    kind: BlockKind
    text: str
    page: int | None = Field(default=None, description="1-based page number (PDF only).")
    section: str | None = Field(default=None, description="Nearest preceding heading.")
    is_contact: bool = Field(
        default=False,
        description="From a header/footer or contact block; candidate for redaction.",
    )


class SourceDocument(BaseModel):
    doc_type: DocType
    path: str = Field(description="Path relative to the candidates directory.")
    sha256: str
    text: str = Field(description="Normalised text as light markdown, in reading order.")
    blocks: list[TextBlock] = Field(default_factory=list)
    extractor: str = Field(description="Which extractor produced this, e.g. 'pdfplumber'.")
    quality: float = Field(ge=0, le=1, description="Heuristic extraction quality.")
    warnings: list[str] = Field(default_factory=list)


class CandidateDocuments(BaseModel):
    candidate_id: str = Field(description="Pseudonymous ID; never the candidate's name.")
    documents: list[SourceDocument] = Field(default_factory=list)
    errors: list[str] = Field(
        default_factory=list, description="Files that could not be read, with the reason."
    )


class ExperienceEntry(BaseModel):
    title: str
    organisation: str | None = None
    start: date | None = None
    end: date | None = Field(default=None, description="None if current or not stated.")
    is_current: bool = False
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
    years_experience: float | None = Field(
        default=None, description="Total years in dated roles, overlaps counted once."
    )
    dropped_quotes: int = Field(
        default=0, description="Model quotes not found in the documents and discarded."
    )


# --- Scoring -----------------------------------------------------------------


class RequirementStatus(StrEnum):
    PASS = "pass"
    PARTIAL = "partial"  # shown, but below the evidence level asked for
    FAIL = "fail"
    UNCLEAR = "unclear"


class RequirementCheck(BaseModel):
    requirement_id: str
    description: str = ""
    must_have: bool = True
    status: RequirementStatus
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)
    required_level: EvidenceLevel | None = None
    observed_level: str | None = Field(
        default=None, description="none / mentioned / applied / proven, as verified."
    )
    value: float | None = Field(default=None, description="0-1 credit towards the score.")


class SkillCheck(BaseModel):
    """How one key skill shows in the documents."""

    skill: str
    required_level: EvidenceLevel
    observed_level: str = "none"
    value: float = 0.0
    found_in: list[str] = Field(
        default_factory=list, description="Where the word appears: 'work', 'skills list'."
    )
    evidence: list[Evidence] = Field(default_factory=list)


class DimensionScore(BaseModel):
    dimension: Dimension
    score: float | None = Field(
        ge=0, le=1, description="None means insufficient evidence (treated as neutral)."
    )
    confidence: float = Field(ge=0, le=1)
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)


class TraitScore(BaseModel):
    trait_id: str
    name: str
    score: float | None = Field(ge=0, le=1, description="None means insufficient evidence.")
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
    skill_checks: list[SkillCheck] = Field(default_factory=list)
    scores: list[DimensionScore] = Field(default_factory=list)
    trait_scores: list[TraitScore] = Field(default_factory=list)
    features: dict[str, float | None] = Field(
        default_factory=dict, description="Deterministic facts, e.g. years of experience."
    )
    total: float | None = None
    rank: int | None = None
    # Totals from repeated scoring differ by this much (None: scored once).
    spread: float | None = None
    # Candidates whose totals are too close to tell apart share a tier (1 = best).
    tier: int | None = None
    flags: list[Flag] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="Why flags were raised, etc.")
    trace_id: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)

    def score(self, dimension: Dimension) -> DimensionScore | None:
        return next((s for s in self.scores if s.dimension is dimension), None)
