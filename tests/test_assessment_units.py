"""Unit tests for evidence checks, redaction, extraction, job profiles, scoring and ranking."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cvscore.config import ModelRole, RankingConfig
from cvscore.evidence import quote_in, verify
from cvscore.extract import CandidateProfileDraft, extract_profile, parse_date, years_of_experience
from cvscore.job import JobProfileDraft, draft_job_profile, load_job_profile, save_job_profile
from cvscore.models import (
    BlockKind,
    CandidateDocuments,
    CandidateProfile,
    Dimension,
    DimensionScore,
    DocType,
    ExperienceEntry,
    Flag,
    JobProfile,
    Requirement,
    RequirementCheck,
    RequirementStatus,
    ScoreCard,
    SourceDocument,
    TextBlock,
    Trait,
)
from cvscore.rank import rank, total_score
from cvscore.redact import redact, redact_blocks
from cvscore.score import ScoringDraft, score_candidate, skill_coverage, to_unit
from tests.fakes import FakeLLM

CV_TEXT = (
    "## Experience\n\nSenior Data Engineer, Acme Analytics, 2019 - present\n\n"
    "- Led a team of 5 engineers building the streaming platform.\n"
    "- Reduced pipeline failures by 40% with data-quality monitoring.\n\n"
    "## Skills\n\n- Python, SQL, Apache Spark"
)
DOCS = {DocType.CV: CV_TEXT, DocType.COVER_LETTER: "I enjoy mentoring engineers and learning."}


# --- evidence -------------------------------------------------------------------------


def test_quote_in_tolerates_formatting() -> None:
    assert quote_in("led a team of 5 engineers building the streaming platform", CV_TEXT)
    assert quote_in("Reduced pipeline failures by 40% with data\u2013quality monitoring", CV_TEXT)
    assert quote_in("Python, SQL, Apache Spark", CV_TEXT)
    assert not quote_in("Led a team of 50 engineers", CV_TEXT)
    assert not quote_in("Python", CV_TEXT)  # too short to count as evidence


def test_verify_falls_back_to_other_documents() -> None:
    kept, dropped = verify(
        [("cv", "I enjoy mentoring engineers"), ("cv", "invented achievement text here")], DOCS
    )
    assert [(e.doc_type, e.quote) for e in kept] == [
        (DocType.COVER_LETTER, "I enjoy mentoring engineers")
    ]
    assert dropped == 1


# --- redaction ------------------------------------------------------------------------


def doc(doc_type: DocType, blocks: list[TextBlock]) -> SourceDocument:
    return SourceDocument(
        doc_type=doc_type, path="x", sha256="0", text="", blocks=blocks, extractor="t", quality=1
    )


def test_redact() -> None:
    cand = CandidateDocuments(
        candidate_id="c-1",
        documents=[
            doc(
                DocType.CV,
                [
                    TextBlock(kind=BlockKind.HEADING, text="Jane Doe", is_contact=True),
                    TextBlock(kind=BlockKind.PARAGRAPH, text="Date of birth: 1 May 1990"),
                    TextBlock(kind=BlockKind.PARAGRAPH, text="Nationality: Dutch"),
                    TextBlock(kind=BlockKind.HEADING, text="Experience"),
                    TextBlock(
                        kind=BlockKind.LIST_ITEM,
                        text="Jane led 5 engineers; mail jane@example.com or +44 20 7946 0958",
                    ),
                ],
            ),
            doc(
                DocType.COVER_LETTER,
                [TextBlock(kind=BlockKind.PARAGRAPH, text="Kind regards, Jane Doe")],
            ),
            doc(DocType.OTHER, [TextBlock(kind=BlockKind.PARAGRAPH, text="x", is_contact=True)]),
        ],
    )
    out = redact(cand, ["Jane Doe"])
    assert out[DocType.CV] == (
        "[REDACTED: contact details]\n\n[REDACTED: personal details]\n\n## Experience\n\n"
        "- [REDACTED: name] led 5 engineers; mail [REDACTED: email] or [REDACTED: phone]"
    )
    assert out[DocType.COVER_LETTER] == "Kind regards, [REDACTED: name]"
    assert DocType.OTHER not in out  # nothing left but markers


def test_redact_name_only_when_capitalised() -> None:
    block = TextBlock(kind=BlockKind.PARAGRAPH, text="Mark set a high-water mark; MARK it.")
    out = redact_blocks([block], ["Mark Olsen"])
    assert out[0].text == "[REDACTED: name] set a high-water mark; [REDACTED: name] it."


# --- extraction -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2021-03", date(2021, 3, 1)),
        ("2021", date(2021, 1, 1)),
        ("03/2021", date(2021, 3, 1)),
        ("Mar 2021", date(2021, 3, 1)),
        ("März 2021", date(2021, 3, 1)),
        ("Sept. 2019", date(2019, 9, 1)),
        ("present", None),
        (None, None),
        ("2021-13", None),
    ],
)
def test_parse_date(value: str | None, expected: date | None) -> None:
    assert parse_date(value) == expected


def test_years_of_experience_counts_overlaps_once() -> None:
    entries = [
        ExperienceEntry(title="A", start=date(2015, 1, 1), end=date(2019, 1, 1)),
        ExperienceEntry(title="B", start=date(2018, 1, 1), end=date(2020, 1, 1)),  # overlaps A
        ExperienceEntry(title="C", start=date(2022, 1, 1), is_current=True),
        ExperienceEntry(title="undated"),
    ]
    assert years_of_experience(entries, today=date(2024, 1, 1)) == 7.0
    assert years_of_experience([]) is None


def candidate_draft() -> dict[str, Any]:
    return {
        "experience": [
            {
                "title": "Senior Data Engineer",
                "organisation": "Acme Analytics",
                "start": "2019",
                "end": "2022",  # ignored: role is current
                "is_current": True,
                "seniority": "senior",
                "domain": "analytics",
                "responsibilities": ["Led a team"],
                "technologies": ["Spark"],
                "evidence_doc": "cv",
                "evidence_quote": "Senior Data Engineer, Acme Analytics, 2019 - present",
            }
        ],
        "achievements": [
            {
                "statement": "Cut failures by 40%",
                "metric": "40%",
                "experience_index": 7,  # out of range -> None
                "evidence_doc": "cv",
                "evidence_quote": "Reduced pipeline failures by 40%",
            },
            {
                "statement": "Won an award",
                "metric": None,
                "experience_index": 0,
                "evidence_doc": "cv",
                "evidence_quote": "Won the national data award in 2020",  # not in the CV
            },
        ],
        "skills": ["Python", "SQL"],
        "education": [{"degree": "MSc", "field": "CS", "institution": None, "end": "2016"}],
        "certifications": [],
        "languages": ["English"],
        "trait_evidence": [
            {
                "label": "mentoring",
                "evidence_doc": "cover_letter",
                "quote": "I enjoy mentoring engineers",
            }
        ],
    }


def test_extract_profile() -> None:
    fake = FakeLLM({CandidateProfileDraft: lambda _: candidate_draft()})
    profile = extract_profile("c-1", DOCS, fake.provider())
    assert fake.calls[0][0] is ModelRole.EXTRACTOR
    exp = profile.experience[0]
    assert exp.start == date(2019, 1, 1) and exp.end is None and exp.is_current
    assert exp.evidence[0].doc_type is DocType.CV
    assert [a.statement for a in profile.achievements] == ["Cut failures by 40%"]
    assert profile.achievements[0].experience_index is None
    assert profile.dropped_quotes == 1
    assert profile.trait_evidence[0].label == "mentoring"
    assert profile.education[0].end == date(2016, 1, 1)
    assert profile.years_experience is not None and profile.years_experience > 4


# --- job profile ----------------------------------------------------------------------


def job_draft() -> dict[str, Any]:
    return {
        "title": "Senior Data Engineer",
        "seniority": "senior",
        "domain": "analytics",
        "summary": "Build data platforms.",
        "responsibilities": ["Build pipelines", "Lead engineers"],
        "must_have": [{"description": "5+ years in data engineering"}],
        "nice_to_have": [
            {"description": "Spark", "importance": "high"},
            {"description": "dbt", "importance": "low"},
        ],
        "key_skills": ["Python", "SQL", "Apache Spark", "Kafka"],
        "traits": [
            {"name": "Mentoring", "definition": "Helps others grow.", "evidence_examples": []}
        ],
        "screening": [
            {
                "name": "Data programming",
                "terms": ["Python", "SQL", "python", " ", "Scala"],
                "required": True,
                "importance": "high",
            },
            {"name": "Nothing to search", "terms": [], "required": False, "importance": "low"},
        ],
    }


def test_job_profile_draft_and_yaml_round_trip(tmp_path: Path) -> None:
    fake = FakeLLM({JobProfileDraft: lambda _: job_draft()})
    job = draft_job_profile("We are hiring...", fake.provider())
    assert fake.calls[0][0] is ModelRole.AGENT
    assert job.id == "senior-data-engineer"
    assert [r.id for r in job.must_have] == ["M1"]
    assert [(r.id, r.weight) for r in job.nice_to_have] == [("N1", 2.0), ("N2", 0.5)]
    assert job.traits[0].id == "T1"
    assert job.weights[Dimension.TRAITS] == 0.0
    # Duplicate and blank terms dropped; a criterion without terms is left out.
    [criterion] = job.screening
    assert (criterion.id, criterion.terms, criterion.required, criterion.weight) == (
        "S1",
        ["Python", "SQL", "Scala"],
        True,
        2.0,
    )
    path = tmp_path / "job.yaml"
    save_job_profile(job, path)
    assert path.read_text().startswith("# Hiring profile")
    assert load_job_profile(path) == job


# --- scoring --------------------------------------------------------------------------


def make_job() -> JobProfile:
    return JobProfile(
        id="job",
        title="Senior Data Engineer",
        must_have=[
            Requirement(id="M1", description="Data engineering"),
            Requirement(id="M2", description="Team lead"),
        ],
        nice_to_have=[Requirement(id="N1", description="Kafka")],
        key_skills=["Python", "Apache Spark", "Kafka"],
        traits=[
            Trait(id="T1", name="Mentoring", definition="Helps others grow."),
            Trait(id="T2", name="Curiosity", definition="Learns new things."),
        ],
    )


def scoring_draft(**overrides: Any) -> dict[str, Any]:
    q = {"doc": "cv", "quote": "Led a team of 5 engineers building the streaming platform"}
    draft = {
        "requirements": [
            {
                "requirement_id": "M1",
                "status": "pass",
                "rationale": "Data engineer.",
                "quotes": [q],
            },
            {"requirement_id": "M2", "status": "pass", "rationale": "Says so.", "quotes": []},
        ],
        "experience": {"score": 5, "confidence": "high", "rationale": "Relevant.", "quotes": [q]},
        "achievements": {"score": 4, "confidence": "high", "rationale": "Good.", "quotes": []},
        "skills": {"score": 3, "confidence": "medium", "rationale": "Some.", "quotes": [q]},
        "traits": [
            {
                "trait_id": "T1",
                "score": 5,
                "rationale": "Mentions mentoring.",
                "quotes": [{"doc": "cover_letter", "quote": "I enjoy mentoring engineers"}],
            },
            {"trait_id": "T2", "score": 4, "rationale": "Assumed.", "quotes": []},
        ],
    }
    draft.update(overrides)
    return draft


def test_skill_coverage() -> None:
    profile = CandidateProfile(candidate_id="c", skills=["python"])
    coverage, matched = skill_coverage(["Python", "Apache Spark", "Kafka"], profile, DOCS)
    assert matched == ["Python", "Apache Spark"]
    assert coverage == 0.67


def test_score_candidate_guards_unsupported_judgements() -> None:
    fake = FakeLLM({ScoringDraft: lambda _: scoring_draft()})
    profile = CandidateProfile(candidate_id="c-1", years_experience=5.0)
    card = score_candidate(make_job(), profile, DOCS, fake.provider())
    assert fake.calls[0][0] is ModelRole.SCORER

    checks = {c.requirement_id: c for c in card.requirement_checks}
    assert checks["M1"].status is RequirementStatus.PASS
    assert checks["M2"].status is RequirementStatus.UNCLEAR  # "pass" without evidence
    assert "Downgraded" in checks["M2"].rationale
    assert checks["N1"].status is RequirementStatus.UNCLEAR  # not assessed at all
    assert not checks["N1"].must_have

    req = card.score(Dimension.REQUIREMENTS)
    assert req is not None and req.score == 0.75  # (1 + 0.5) / 2
    exp = card.score(Dimension.EXPERIENCE)
    assert exp is not None and exp.score == 1.0 and exp.confidence == 0.9
    ach = card.score(Dimension.ACHIEVEMENTS)
    assert ach is not None and ach.score == 0.75 and ach.confidence == 0.3  # no evidence
    assert any("achievements: high score without verifiable evidence" in n for n in card.notes)

    traits = {t.trait_id: t for t in card.trait_scores}
    assert traits["T1"].score == 1.0
    assert traits["T2"].score is None  # no evidence -> insufficient, not low
    trait_dim = card.score(Dimension.TRAITS)
    assert trait_dim is not None and trait_dim.score == 1.0 and trait_dim.confidence == 0.5
    assert card.features["years_experience"] == 5.0
    assert card.features["skill_coverage"] == 0.67


def test_to_unit_clamps() -> None:
    assert [to_unit(s) for s in (0, 1, 3, 5, 9)] == [0.0, 0.0, 0.5, 1.0, 1.0]


# --- ranking --------------------------------------------------------------------------


def card(
    cid: str, scores: dict[Dimension, float | None], checks: list[RequirementStatus]
) -> ScoreCard:
    return ScoreCard(
        candidate_id=cid,
        job_id="job",
        scores=[
            DimensionScore(dimension=d, score=s, confidence=0.9, rationale="")
            for d, s in scores.items()
        ],
        requirement_checks=[
            RequirementCheck(requirement_id=f"M{i}", status=st, rationale="")
            for i, st in enumerate(checks, 1)
        ],
    )


def test_total_score_renormalises_over_scored_dimensions() -> None:
    weights = {Dimension.EXPERIENCE: 0.5, Dimension.SKILLS: 0.5, Dimension.TRAITS: 0.0}
    c = card("a", {Dimension.EXPERIENCE: 1.0, Dimension.SKILLS: None, Dimension.TRAITS: 0.0}, [])
    assert total_score(c, weights) == 1.0
    assert total_score(card("b", {}, []), weights) is None


def test_rank_orders_and_flags() -> None:
    job = make_job()
    good = card(
        "good", {Dimension.EXPERIENCE: 0.9, Dimension.SKILLS: 0.8}, [RequirementStatus.PASS]
    )
    better_but_ko = card(
        "ko", {Dimension.EXPERIENCE: 1.0, Dimension.SKILLS: 1.0}, [RequirementStatus.FAIL]
    )
    unclear = card(
        "unclear", {Dimension.EXPERIENCE: 0.5, Dimension.SKILLS: 0.5}, [RequirementStatus.UNCLEAR]
    )
    unreadable = ScoreCard(candidate_id="none", job_id="job", flags=[Flag.NEEDS_REVIEW])
    unclear.scores[0].confidence = 0.2

    ordered = rank([unreadable, better_but_ko, unclear, good], job, RankingConfig())
    assert [c.candidate_id for c in ordered] == ["good", "unclear", "ko", "none"]
    assert [c.rank for c in ordered] == [1, 2, 3, 4]
    assert Flag.KNOCKOUT_FAILED in better_but_ko.flags and better_but_ko.total == 1.0
    assert set(unclear.flags) == {Flag.NEEDS_REVIEW, Flag.LOW_CONFIDENCE}

    excluded = rank([better_but_ko], job, RankingConfig(knockout="exclude"))
    assert excluded[0].total is None


def test_screening_falls_back_to_key_skills() -> None:
    draft = {**job_draft(), "screening": []}
    job = draft_job_profile("ad", FakeLLM({JobProfileDraft: lambda _: draft}).provider())
    assert [(c.name, c.terms, c.required) for c in job.screening] == [
        ("Python", ["Python"], False),
        ("SQL", ["SQL"], False),
        ("Apache Spark", ["Apache Spark"], False),
        ("Kafka", ["Kafka"], False),
    ]
