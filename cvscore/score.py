"""Scores one candidate against a job profile.

One structured-output call applies the rubric (requirements, experience, achievements,
skills, traits) with quotes as evidence. Deterministic checks then guard against
unsupported judgements: quotes are verified against the documents, a "pass" without
verifiable evidence becomes "unclear", and a high score without evidence gets low
confidence. Skill coverage and years of experience are computed without the model.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from cvscore.config import ModelRole
from cvscore.evidence import verify
from cvscore.extract import DocName, documents_prompt
from cvscore.llm.structured import LLMProvider
from cvscore.models import (
    CandidateProfile,
    Dimension,
    DimensionScore,
    DocType,
    Evidence,
    JobProfile,
    RequirementCheck,
    RequirementStatus,
    ScoreCard,
    TraitScore,
)
from cvscore.prompts import PROMPT_VERSIONS, SCORING_SYSTEM

# --- Model-facing schema (strict: all fields required, no defaults) -------------------

Confidence = Literal["low", "medium", "high"]


class _Quote(BaseModel):
    doc: DocName
    quote: str


class _RequirementAssessment(BaseModel):
    requirement_id: str
    status: Literal["pass", "fail", "unclear"]
    rationale: str
    quotes: list[_Quote]


class _DimensionAssessment(BaseModel):
    score: int
    confidence: Confidence
    rationale: str
    quotes: list[_Quote]


class _TraitAssessment(BaseModel):
    trait_id: str
    score: int | None
    rationale: str
    quotes: list[_Quote]


class ScoringDraft(BaseModel):
    requirements: list[_RequirementAssessment]
    experience: _DimensionAssessment
    achievements: _DimensionAssessment
    skills: _DimensionAssessment
    traits: list[_TraitAssessment]


_CONFIDENCE = {"low": 0.3, "medium": 0.6, "high": 0.9}
_STATUS_VALUE = {
    RequirementStatus.PASS: 1.0,
    RequirementStatus.UNCLEAR: 0.5,
    RequirementStatus.FAIL: 0.0,
}


def to_unit(score: int) -> float:
    """1..5 rubric score -> 0..1."""
    return (min(5, max(1, score)) - 1) / 4


def skill_coverage(
    key_skills: list[str], profile: CandidateProfile, docs: dict[DocType, str]
) -> tuple[float | None, list[str]]:
    """Share of the job's key skills that appear in the profile or documents (lexical)."""
    if not key_skills:
        return None, []
    corpus = " ".join(
        [*profile.skills, *(t for e in profile.experience for t in e.technologies), *docs.values()]
    ).lower()
    matched = []
    for skill in key_skills:
        tokens = [t for t in re.findall(r"[\w+#.]+", skill.lower()) if len(t) >= 2]
        phrase = re.escape(skill.lower().strip())
        if re.search(rf"(?<!\w){phrase}(?!\w)", corpus) or (
            tokens and all(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", corpus) for t in tokens)
        ):
            matched.append(skill)
    return round(len(matched) / len(key_skills), 2), matched


def _profile_summary(profile: CandidateProfile) -> str:
    data = profile.model_dump(
        mode="json",
        exclude={
            "candidate_id": True,
            "dropped_quotes": True,
            "trait_evidence": True,
            "experience": {"__all__": {"evidence"}},
            "achievements": {"__all__": {"evidence"}},
        },
    )
    return json.dumps(data, ensure_ascii=False, indent=1)


def _job_summary(job: JobProfile) -> str:
    data = job.model_dump(mode="json", exclude={"weights", "id"})
    for t in data["traits"]:
        t.pop("weight", None)
    return json.dumps(data, ensure_ascii=False, indent=1)


def score_candidate(
    job: JobProfile,
    profile: CandidateProfile,
    docs: dict[DocType, str],
    llm: LLMProvider,
    config: RunnableConfig | None = None,
) -> ScoreCard:
    coverage, matched = skill_coverage(job.key_skills, profile, docs)
    facts = {
        "years_experience": profile.years_experience,
        "key_skills_found": matched,
        "key_skills_missing": [s for s in job.key_skills if s not in matched],
    }
    prompt = (
        f"<hiring_profile>\n{_job_summary(job)}\n</hiring_profile>\n\n"
        f"<candidate_profile>\n{_profile_summary(profile)}\n</candidate_profile>\n\n"
        f"<computed_facts>\n{json.dumps(facts, ensure_ascii=False)}\n</computed_facts>\n\n"
        f"{documents_prompt(docs)}"
    )
    draft = llm(ModelRole.SCORER).generate(
        ScoringDraft, [SystemMessage(SCORING_SYSTEM), HumanMessage(prompt)], config
    )

    notes: list[str] = []
    dropped_total = 0

    def evidence(quotes: list[_Quote]) -> list[Evidence]:
        nonlocal dropped_total
        kept, dropped = verify([(q.doc, q.quote) for q in quotes], docs)
        dropped_total += dropped
        return kept

    # Requirements: every must-have and nice-to-have gets a check, assessed or not.
    by_id = {r.requirement_id: r for r in draft.requirements}
    checks: list[RequirementCheck] = []
    for req, must in [*((r, True) for r in job.must_have), *((r, False) for r in job.nice_to_have)]:
        a = by_id.get(req.id)
        if a is None:
            checks.append(
                RequirementCheck(
                    requirement_id=req.id,
                    description=req.description,
                    must_have=must,
                    status=RequirementStatus.UNCLEAR,
                    rationale="Not assessed by the model.",
                )
            )
            continue
        ev = evidence(a.quotes)
        status = RequirementStatus(a.status)
        rationale = a.rationale
        if status is RequirementStatus.PASS and not ev:
            status = RequirementStatus.UNCLEAR
            rationale += " [Downgraded from pass: no quote could be found in the documents.]"
        checks.append(
            RequirementCheck(
                requirement_id=req.id,
                description=req.description,
                must_have=must,
                status=status,
                rationale=rationale,
                evidence=ev,
            )
        )

    scores: list[DimensionScore] = []
    must_checks = [c for c in checks if c.must_have]
    if must_checks:
        weights = {r.id: r.weight for r in job.must_have}
        total_w = sum(weights[c.requirement_id] for c in must_checks) or 1.0
        value = (
            sum(_STATUS_VALUE[c.status] * weights[c.requirement_id] for c in must_checks) / total_w
        )
        unclear = sum(c.status is RequirementStatus.UNCLEAR for c in must_checks)
        scores.append(
            DimensionScore(
                dimension=Dimension.REQUIREMENTS,
                score=round(value, 3),
                confidence=round(1 - unclear / len(must_checks) * 0.7, 2),
                rationale=", ".join(f"{c.requirement_id}: {c.status}" for c in must_checks),
            )
        )

    for dimension, assessment in [
        (Dimension.EXPERIENCE, draft.experience),
        (Dimension.ACHIEVEMENTS, draft.achievements),
        (Dimension.SKILLS, draft.skills),
    ]:
        ev = evidence(assessment.quotes)
        confidence = _CONFIDENCE[assessment.confidence]
        if assessment.score >= 4 and not ev:
            confidence = _CONFIDENCE["low"]
            notes.append(f"{dimension}: high score without verifiable evidence.")
        scores.append(
            DimensionScore(
                dimension=dimension,
                score=to_unit(assessment.score),
                confidence=confidence,
                rationale=assessment.rationale,
                evidence=ev,
            )
        )

    trait_by_id = {t.trait_id: t for t in draft.traits}
    trait_scores: list[TraitScore] = []
    for trait in job.traits:
        t = trait_by_id.get(trait.id)
        ev = evidence(t.quotes) if t else []
        # A trait score needs evidence; without it, it is "insufficient evidence".
        trait_value = to_unit(t.score) if t and t.score is not None and ev else None
        trait_scores.append(
            TraitScore(
                trait_id=trait.id,
                name=trait.name,
                score=trait_value,
                rationale=(t.rationale if t else "Not assessed by the model."),
                evidence=ev,
            )
        )
    scored = [(ts, tr.weight) for ts, tr in zip(trait_scores, job.traits, strict=True)]
    scored = [(ts, w) for ts, w in scored if ts.score is not None and w > 0]
    if job.traits:
        traits_value: float | None = (
            sum(ts.score * w for ts, w in scored if ts.score is not None)
            / sum(w for _, w in scored)
            if scored
            else None
        )
        scores.append(
            DimensionScore(
                dimension=Dimension.TRAITS,
                score=round(traits_value, 3) if traits_value is not None else None,
                confidence=round(len(scored) / len(job.traits), 2),
                rationale=f"{len(scored)} of {len(job.traits)} traits had evidence.",
            )
        )

    dropped_total += profile.dropped_quotes
    if dropped_total:
        notes.append(f"{dropped_total} quotes from the model were not found in the documents.")

    return ScoreCard(
        candidate_id=profile.candidate_id,
        job_id=job.id,
        requirement_checks=checks,
        scores=scores,
        trait_scores=trait_scores,
        features={
            "years_experience": profile.years_experience,
            "skill_coverage": coverage,
            "dropped_quotes": float(dropped_total),
        },
        notes=notes,
        prompt_versions=dict(PROMPT_VERSIONS),
    )
