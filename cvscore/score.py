"""Scores one candidate against a job profile.

One structured-output call gathers many small judgements, each backed by quotes:

- **Requirements** (must-haves and nice-to-haves) and **key skills**: the highest evidence
  level the documents show (none / mentioned / applied / proven), judged with each item's
  match mode (exact, or comparable alternatives). Python turns levels into credit against
  the level each item asks for (``evidence``, default "applied"), so a skill that is only
  listed earns half when use in work is required.
- **Experience**: relevance and seniority (1-5) from the model, plus years and recency of
  the relevant roles computed from their dates.
- **Achievements** and **traits**: 1-5 rubric scores.

Deterministic checks then guard against unsupported judgements: quotes are verified
against the documents, a level without a verifiable quote counts as none, and scores the
model was unsure about are pulled towards the middle. Where a skill's name occurs (work
history or a skills list) is found without the model and given to it as a fact.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from cvscore.config import ModelRole
from cvscore.contexts import context_level, term_contexts
from cvscore.evidence import verify
from cvscore.extract import DocName, documents_prompt, years_of_experience
from cvscore.llm.structured import LLMProvider
from cvscore.models import (
    LEVEL_RANK,
    CandidateProfile,
    Dimension,
    DimensionScore,
    DocType,
    Evidence,
    JobProfile,
    RequirementCheck,
    RequirementStatus,
    ScoreCard,
    SkillCheck,
    SourceDocument,
    TextBlock,
    TraitScore,
    level_value,
)
from cvscore.prompts import PROMPT_VERSIONS, SCORING_SYSTEM

# --- Model-facing schema (strict: all fields required, no defaults) -------------------

Confidence = Literal["low", "medium", "high"]
Level = Literal["none", "mentioned", "applied", "proven"]


class _Quote(BaseModel):
    doc: DocName
    quote: str


class _RequirementAssessment(BaseModel):
    requirement_id: str
    level: Level
    contradicted: bool
    rationale: str
    quotes: list[_Quote]


class _SkillAssessment(BaseModel):
    skill: str
    level: Level
    quotes: list[_Quote]


class _ExperienceAssessment(BaseModel):
    relevant_roles: list[int]
    relevance: int
    seniority: int
    confidence: Confidence
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
    skills: list[_SkillAssessment]
    experience: _ExperienceAssessment
    achievements: _DimensionAssessment
    traits: list[_TraitAssessment]


_CONFIDENCE = {"low": 0.3, "medium": 0.6, "high": 0.9}
# How far a model-judged score keeps its distance from the middle (0.5) at each
# confidence: an unsure 5 should not top the list on its own.
_SHRINK = {"low": 0.6, "medium": 0.85, "high": 1.0}
UNSTATED_VALUE = 0.25  # credit for a requirement the documents do not show at all
MUST_HAVE_SHARE = 2 / 3  # of the requirements score; nice-to-haves get the rest
# Experience = these shares of relevance, seniority, duration and recency.
EXPERIENCE_PARTS = {"relevance": 0.45, "seniority": 0.25, "duration": 0.20, "recency": 0.10}
_SENIORITY_YEARS = [
    (r"intern|trainee|graduate|junior|entry", 1.0),
    (r"principal|staff|lead|head|director|chief|architect", 8.0),
    (r"senior|sr\b|expert", 6.0),
    (r"mid|intermediate|medior|experienced", 3.0),
]
DEFAULT_TARGET_YEARS = 4.0


def to_unit(score: int) -> float:
    """1..5 rubric score -> 0..1."""
    return (min(5, max(1, score)) - 1) / 4


def shrink(value: float, confidence: str) -> float:
    """Pull a model-judged 0..1 score towards 0.5 when the model was unsure."""
    return 0.5 + (value - 0.5) * _SHRINK.get(confidence, 1.0)


def target_years(job: JobProfile) -> float:
    """Years of relevant experience the role calls for: from the profile, else from the
    seniority or title."""
    if job.target_years:
        return job.target_years
    text = f"{job.seniority or ''} {job.title}".lower()
    for pattern, years in _SENIORITY_YEARS:
        if re.search(pattern, text):
            return years
    return DEFAULT_TARGET_YEARS


def recency_value(years_since: float | None) -> float:
    """1 for a current role or one that ended within a year, falling to 0 after 6 years."""
    if years_since is None:
        return 0.0
    return round(max(0.0, min(1.0, 1 - (years_since - 1) / 5)), 3)


def relevant_experience(
    profile: CandidateProfile, indices: list[int], today: date | None = None
) -> tuple[float | None, float | None]:
    """(years in the relevant roles, years since the most recent of them ended)."""
    today = today or date.today()
    roles = [
        profile.experience[i] for i in dict.fromkeys(indices) if 0 <= i < len(profile.experience)
    ]
    if not roles:
        return None, None
    years = years_of_experience(roles, today=today)
    dated: list[date] = [d for r in roles if (d := today if r.is_current else r.end)]
    since = round(max(0.0, (today - max(dated)).days / 365.25), 1) if dated else None
    return years, since


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


def skill_places(
    key_skills: list[str], profile: CandidateProfile, blocks: list[list[TextBlock]]
) -> dict[str, list[str]]:
    """Where each key skill's name occurs: 'work' (an experience entry or a work/project
    section) and/or 'skills list' (skills section, summary, education, ...)."""
    places: dict[str, list[str]] = {}
    for skill in key_skills:
        found = {c for doc_blocks in blocks for c in term_contexts([skill], doc_blocks)}
        lowered = skill.lower()
        for e in profile.experience:
            if any(lowered == t.lower() for t in e.technologies):
                found.add("work")
        if not found and any(lowered == s.lower() for s in profile.skills):
            found.add("skills list")
        places[skill] = sorted(found, key=["work", "skills list"].index)
    return places


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
    for i, entry in enumerate(data["experience"]):
        entry["index"] = i
    return json.dumps(data, ensure_ascii=False, indent=1)


def job_summary(job: JobProfile, reverse: bool = False) -> str:
    data = job.model_dump(
        mode="json",
        exclude={"weights", "id", "evidence_default", "match_default", "screening"},
    )
    for t in data["traits"]:
        t.pop("weight", None)
    for key in ("must_have", "nice_to_have"):
        for item, req in zip(data[key], getattr(job, key), strict=True):
            item["evidence"] = job.evidence_for(req).value
            item["match"] = job.match_for(req).value
            item.pop("weight", None)
    data["key_skills_evidence"] = job.evidence_default.value
    if reverse:  # a second reading sees the lists in another order
        for key in ("must_have", "nice_to_have", "key_skills", "responsibilities"):
            data[key] = list(reversed(data[key]))
    return json.dumps(data, ensure_ascii=False, indent=1)


def score_candidate(
    job: JobProfile,
    profile: CandidateProfile,
    docs: dict[DocType, str],
    llm: LLMProvider,
    config: RunnableConfig | None = None,
    *,
    sources: list[SourceDocument] | None = None,
    variant: int = 0,
    today: date | None = None,
) -> ScoreCard:
    """``sources`` are the candidate's extracted documents, used only to see where skill
    names occur (nothing from them goes to the model). ``variant`` > 0 is a repeated
    reading: lists and documents are presented in another order, so its answer is an
    independent sample rather than a cached copy."""
    blocks = [d.blocks for d in (sources or [])]
    coverage, matched = skill_coverage(job.key_skills, profile, docs)
    places = skill_places(job.key_skills, profile, blocks)
    facts: dict[str, object] = {
        "years_experience_total": profile.years_experience,
        "key_skills_found_in": {s: p or ["not found"] for s, p in places.items()},
    }
    if variant:
        facts["assessment_pass"] = variant + 1
    shown_docs = dict(reversed(list(docs.items()))) if variant else docs
    prompt = (
        f"<hiring_profile>\n{job_summary(job, reverse=bool(variant))}\n</hiring_profile>\n\n"
        f"<candidate_profile>\n{_profile_summary(profile)}\n</candidate_profile>\n\n"
        f"<computed_facts>\n{json.dumps(facts, ensure_ascii=False)}\n</computed_facts>\n\n"
        f"{documents_prompt(shown_docs)}"
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

    # --- Requirements: a level per item, credited against the level it asks for.
    by_id = {r.requirement_id: r for r in draft.requirements}
    checks: list[RequirementCheck] = []
    for req, must in [*((r, True) for r in job.must_have), *((r, False) for r in job.nice_to_have)]:
        required = job.evidence_for(req)
        a = by_id.get(req.id)
        if a is None:
            checks.append(
                RequirementCheck(
                    requirement_id=req.id,
                    description=req.description,
                    must_have=must,
                    status=RequirementStatus.UNCLEAR,
                    rationale="Not assessed by the model.",
                    required_level=required,
                    observed_level="none",
                    value=UNSTATED_VALUE,
                )
            )
            continue
        ev = evidence(a.quotes)
        level: str = a.level
        rationale = a.rationale
        if level != "none" and not ev:
            rationale += f" [Counted as not shown: no quote for '{level}' could be found.]"
            level = "none"
        if a.contradicted:
            status, value = RequirementStatus.FAIL, 0.0
        elif level == "none":
            status, value = RequirementStatus.UNCLEAR, UNSTATED_VALUE
        elif LEVEL_RANK[level] >= LEVEL_RANK[required.value]:
            status, value = RequirementStatus.PASS, 1.0
        else:
            status, value = RequirementStatus.PARTIAL, level_value(level, required)
            rationale += f" [Shown as '{level}'; '{required.value}' is asked for.]"
        checks.append(
            RequirementCheck(
                requirement_id=req.id,
                description=req.description,
                must_have=must,
                status=status,
                rationale=rationale,
                evidence=ev,
                required_level=required,
                observed_level=level,
                value=round(value, 3),
            )
        )

    scores: list[DimensionScore] = []
    weights = {r.id: r.weight for r in [*job.must_have, *job.nice_to_have]}

    def weighted(items: list[RequirementCheck]) -> float | None:
        w = sum(weights[c.requirement_id] for c in items)
        if not items or not w:
            return None
        return sum((c.value or 0.0) * weights[c.requirement_id] for c in items) / w

    must_value = weighted([c for c in checks if c.must_have])
    nice_value = weighted([c for c in checks if not c.must_have])
    if must_value is not None or nice_value is not None:
        if must_value is not None and nice_value is not None:
            value = MUST_HAVE_SHARE * must_value + (1 - MUST_HAVE_SHARE) * nice_value
        else:
            value = must_value if must_value is not None else nice_value or 0.0
        unclear = sum(c.status is RequirementStatus.UNCLEAR for c in checks)
        scores.append(
            DimensionScore(
                dimension=Dimension.REQUIREMENTS,
                score=round(value, 3),
                confidence=round(1 - unclear / len(checks) * 0.7, 2),
                rationale=", ".join(f"{c.requirement_id}: {c.status}" for c in checks),
            )
        )

    # --- Skills: a level per key skill; the model's, or where the word occurs.
    skill_by_name = {s.skill.strip().lower(): s for s in draft.skills}
    skill_checks: list[SkillCheck] = []
    required = job.evidence_default
    for skill in job.key_skills:
        sa = skill_by_name.get(skill.strip().lower())
        ev = evidence(sa.quotes) if sa else []
        # The model's level counts with a verified quote; where the word occurs counts
        # anyway (but never above "applied": proven needs a quote).
        model_level = sa.level if sa and ev else "none"
        placed = context_level(places[skill])
        level = max(model_level, placed, key=LEVEL_RANK.__getitem__)
        skill_checks.append(
            SkillCheck(
                skill=skill,
                required_level=required,
                observed_level=level,
                value=round(level_value(level, required), 3),
                found_in=places[skill],
                evidence=ev,
            )
        )
    if skill_checks:
        quoted = sum(bool(s.evidence) for s in skill_checks)
        listed = [s.skill for s in skill_checks if s.observed_level == "mentioned"]
        missing = [s.skill for s in skill_checks if s.observed_level == "none"]
        parts = []
        if listed:
            parts.append(f"listed but not shown in work: {', '.join(listed)}")
        if missing:
            parts.append(f"not shown: {', '.join(missing)}")
        scores.append(
            DimensionScore(
                dimension=Dimension.SKILLS,
                score=round(sum(s.value for s in skill_checks) / len(skill_checks), 3),
                confidence=0.9 if quoted * 2 >= len(skill_checks) else 0.6,
                rationale="; ".join(parts) or "All key skills shown at the level asked for.",
                evidence=[e for s in skill_checks for e in s.evidence[:1]][:6],
            )
        )

    # --- Experience: relevance and seniority judged, duration and recency computed.
    exp = draft.experience
    exp_ev = evidence(exp.quotes)
    years, since = relevant_experience(profile, exp.relevant_roles, today)
    goal = target_years(job)
    parts_value = {
        "relevance": to_unit(exp.relevance),
        "seniority": to_unit(exp.seniority),
        "duration": round(min(1.0, (years or 0.0) / goal), 3) if goal else 0.0,
        "recency": recency_value(since),
    }
    exp_conf = exp.confidence
    if exp.relevance >= 4 and not exp_ev:
        exp_conf = "low"
        notes.append("experience: high relevance without verifiable evidence.")
    raw = sum(EXPERIENCE_PARTS[k] * v for k, v in parts_value.items())
    years_text = "no dated relevant roles" if years is None else f"{years:g} relevant years"
    scores.append(
        DimensionScore(
            dimension=Dimension.EXPERIENCE,
            score=round(shrink(raw, exp_conf), 3),
            confidence=_CONFIDENCE[exp_conf],
            rationale=(
                f"{exp.rationale} [Relevance {exp.relevance}/5, seniority {exp.seniority}/5, "
                f"{years_text} of {goal:g} asked for"
                + (f", last {since:g} years ago" if since else "")
                + "]"
            ),
            evidence=exp_ev,
        )
    )

    # --- Achievements: one rubric score, pulled to the middle when unsure.
    ach = draft.achievements
    ach_ev = evidence(ach.quotes)
    ach_conf = ach.confidence
    if ach.score >= 4 and not ach_ev:
        ach_conf = "low"
        notes.append("achievements: high score without verifiable evidence.")
    scores.append(
        DimensionScore(
            dimension=Dimension.ACHIEVEMENTS,
            score=round(shrink(to_unit(ach.score), ach_conf), 3),
            confidence=_CONFIDENCE[ach_conf],
            rationale=ach.rationale,
            evidence=ach_ev,
        )
    )

    # --- Traits: only with evidence.
    trait_by_id = {t.trait_id: t for t in draft.traits}
    trait_scores: list[TraitScore] = []
    for trait in job.traits:
        t = trait_by_id.get(trait.id)
        ev = evidence(t.quotes) if t else []
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
        skill_checks=skill_checks,
        scores=scores,
        trait_scores=trait_scores,
        features={
            "years_experience": profile.years_experience,
            "relevant_years": years,
            "years_since_relevant": since,
            "target_years": goal,
            "skill_coverage": coverage,
            "key_skills_matched": float(len(matched)),
            "dropped_quotes": float(dropped_total),
        },
        notes=notes,
        prompt_versions=dict(PROMPT_VERSIONS),
    )
