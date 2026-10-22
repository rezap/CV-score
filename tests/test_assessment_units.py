"""Unit tests for evidence checks, redaction, extraction, job profiles, scoring and ranking."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cvscore.config import ModelRole, RankingConfig
from cvscore.evidence import quote_in, verify
from cvscore.extract import CandidateProfileDraft, extract_profile, parse_date, years_of_experience
from cvscore.job import JobProfileDraft, draft_job_profile, load_job_profile, save_job_profile
from cvscore.judge import PairwiseDraft, judge_ties
from cvscore.models import (
    BlockKind,
    CandidateDocuments,
    CandidateProfile,
    Dimension,
    DimensionScore,
    DocType,
    EvidenceLevel,
    ExperienceEntry,
    Flag,
    JobProfile,
    MatchMode,
    Requirement,
    RequirementCheck,
    RequirementStatus,
    ScoreCard,
    SourceDocument,
    TextBlock,
    Trait,
)
from cvscore.rank import assign_tiers, combine_passes, rank, total_score
from cvscore.redact import redact, redact_blocks
from cvscore.score import (
    ScoringDraft,
    recency_value,
    score_candidate,
    shrink,
    skill_coverage,
    target_years,
    to_unit,
)
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
        "must_have": [
            {
                "description": "5+ years in data engineering",
                "evidence": "applied",
                "match": "equivalent",
            }
        ],
        "nice_to_have": [
            {"description": "Spark", "importance": "high", "evidence": "proven", "match": "exact"},
            {
                "description": "dbt",
                "importance": "low",
                "evidence": "applied",
                "match": "equivalent",
            },
        ],
        "target_years": 5,
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
    # Only settings that differ from the job's defaults are stored on the item.
    assert job.evidence_default is EvidenceLevel.APPLIED and job.target_years == 5
    assert (job.must_have[0].evidence, job.must_have[0].match) == (None, None)
    assert (job.nice_to_have[0].evidence, job.nice_to_have[0].match) == (
        EvidenceLevel.PROVEN,
        MatchMode.EXACT,
    )
    assert job.evidence_for(job.must_have[0]) is EvidenceLevel.APPLIED
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


def make_job(**overrides: Any) -> JobProfile:
    job = JobProfile(
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
        target_years=5,
    )
    return job.model_copy(update=overrides)


LEAD = {"doc": "cv", "quote": "Led a team of 5 engineers building the streaming platform"}
SKILLS = {"doc": "cv", "quote": "Python, SQL, Apache Spark"}


def scoring_draft(**overrides: Any) -> dict[str, Any]:
    draft = {
        "requirements": [
            {
                "requirement_id": "M1",
                "level": "applied",
                "contradicted": False,
                "rationale": "Data engineer.",
                "quotes": [LEAD],
            },
            # A level without a verifiable quote counts as not shown.
            {
                "requirement_id": "M2",
                "level": "proven",
                "contradicted": False,
                "rationale": "Says so.",
                "quotes": [],
            },
        ],
        "skills": [
            {"skill": "Python", "level": "mentioned", "quotes": [SKILLS]},
            {"skill": "Apache Spark", "level": "proven", "quotes": []},  # unverified
            {"skill": "Kafka", "level": "none", "quotes": []},
        ],
        "experience": {
            "relevant_roles": [0],
            "relevance": 5,
            "seniority": 4,
            "confidence": "high",
            "rationale": "Relevant.",
            "quotes": [LEAD],
        },
        "achievements": {"score": 4, "confidence": "high", "rationale": "Good.", "quotes": []},
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


def cv_sources() -> list[SourceDocument]:
    blocks = [
        TextBlock(kind=BlockKind.HEADING, text="Experience"),
        TextBlock(
            kind=BlockKind.LIST_ITEM,
            text="Led a team of 5 engineers building the streaming platform in Python.",
            section="Experience",
        ),
        TextBlock(kind=BlockKind.HEADING, text="Skills"),
        TextBlock(kind=BlockKind.LIST_ITEM, text="Python, SQL, Apache Spark", section="Skills"),
    ]
    return [
        SourceDocument(
            doc_type=DocType.CV,
            path="cv.pdf",
            sha256="x",
            text=CV_TEXT,
            blocks=blocks,
            extractor="test",
            quality=1.0,
        )
    ]


def profile_with_role(**kw: Any) -> CandidateProfile:
    role = ExperienceEntry(
        title="Senior Data Engineer", start=date(2019, 1, 1), is_current=True, **kw
    )
    return CandidateProfile(candidate_id="c-1", years_experience=5.0, experience=[role])


def test_skill_coverage() -> None:
    profile = CandidateProfile(candidate_id="c", skills=["python"])
    coverage, matched = skill_coverage(["Python", "Apache Spark", "Kafka"], profile, DOCS)
    assert matched == ["Python", "Apache Spark"]
    assert coverage == 0.67


def test_score_candidate_levels_and_guards() -> None:
    fake = FakeLLM({ScoringDraft: lambda _: scoring_draft()})
    today = date(2024, 1, 1)
    card = score_candidate(
        make_job(), profile_with_role(), DOCS, fake.provider(), sources=cv_sources(), today=today
    )
    assert fake.calls[0][0] is ModelRole.SCORER

    checks = {c.requirement_id: c for c in card.requirement_checks}
    assert (checks["M1"].status, checks["M1"].value) == (RequirementStatus.PASS, 1.0)
    # "proven" without a quote: counted as not shown, and unclear rather than failed.
    assert checks["M2"].status is RequirementStatus.UNCLEAR and checks["M2"].value == 0.25
    assert checks["M2"].observed_level == "none" and "no quote" in checks["M2"].rationale
    assert checks["N1"].status is RequirementStatus.UNCLEAR  # not assessed at all
    assert not checks["N1"].must_have

    # Must-haves count 2/3, nice-to-haves 1/3: 2/3 * (1 + 0.25) / 2 + 1/3 * 0.25.
    req = card.score(Dimension.REQUIREMENTS)
    assert req is not None and req.score == round(2 / 3 * 0.625 + 1 / 3 * 0.25, 3)

    skills = {s.skill: s for s in card.skill_checks}
    # Python is in a work bullet as well as the skills list: placement makes it "applied".
    assert skills["Python"].observed_level == "applied" and skills["Python"].value == 1.0
    assert skills["Python"].found_in == ["work", "skills list"]
    # Spark: "proven" was not quoted; it only sits in the skills list -> listed, half.
    assert skills["Apache Spark"].observed_level == "mentioned"
    assert skills["Apache Spark"].value == 0.5
    assert skills["Kafka"].observed_level == "none" and skills["Kafka"].value == 0.0
    sk = card.score(Dimension.SKILLS)
    assert sk is not None and sk.score == 0.5 and "Apache Spark" in sk.rationale

    # Experience: relevance 5 (1.0), seniority 4 (0.75), 5 of 5 years, current role.
    exp = card.score(Dimension.EXPERIENCE)
    assert exp is not None and exp.score == pytest.approx(0.45 + 0.25 * 0.75 + 0.2 + 0.1, abs=1e-3)
    assert card.features["relevant_years"] == 5.0 and card.features["years_since_relevant"] == 0.0

    # Achievements: 4 (0.75) with no quote -> low confidence, pulled towards the middle.
    ach = card.score(Dimension.ACHIEVEMENTS)
    assert ach is not None and ach.confidence == 0.3 and ach.score == round(shrink(0.75, "low"), 3)
    assert any("achievements: high score without verifiable evidence" in n for n in card.notes)

    traits = {t.trait_id: t for t in card.trait_scores}
    assert traits["T1"].score == 1.0
    assert traits["T2"].score is None  # no evidence -> insufficient, not low
    trait_dim = card.score(Dimension.TRAITS)
    assert trait_dim is not None and trait_dim.score == 1.0 and trait_dim.confidence == 0.5


def test_evidence_level_decides_credit() -> None:
    """The same CV, with Terraform only in the skills list, earns full credit when a
    mention is enough and half when use in work is asked for."""
    draft = scoring_draft(
        requirements=[
            {
                "requirement_id": "M1",
                "level": "mentioned",
                "contradicted": False,
                "rationale": "Listed.",
                "quotes": [SKILLS],
            }
        ]
    )
    fake = FakeLLM({ScoringDraft: lambda _: draft})
    values = {}
    for level in EvidenceLevel:
        job = make_job(
            must_have=[Requirement(id="M1", description="Spark", evidence=level)], nice_to_have=[]
        )
        card = score_candidate(job, profile_with_role(), DOCS, fake.provider())
        [check] = card.requirement_checks
        values[level] = (check.status, round(check.value or 0, 2))
    assert values == {
        EvidenceLevel.MENTIONED: (RequirementStatus.PASS, 1.0),
        EvidenceLevel.APPLIED: (RequirementStatus.PARTIAL, 0.5),
        EvidenceLevel.PROVEN: (RequirementStatus.PARTIAL, 0.33),
    }
    # A contradiction is a fail whatever the level.
    contradicted = scoring_draft(requirements=[{**draft["requirements"][0], "contradicted": True}])
    card = score_candidate(
        make_job(must_have=[Requirement(id="M1", description="Spark")], nice_to_have=[]),
        profile_with_role(),
        DOCS,
        FakeLLM({ScoringDraft: lambda _: contradicted}).provider(),
    )
    assert card.requirement_checks[0].status is RequirementStatus.FAIL


def test_second_reading_is_a_fresh_call_in_another_order() -> None:
    fake = FakeLLM({ScoringDraft: lambda _: scoring_draft()})
    job = make_job()
    score_candidate(job, profile_with_role(), DOCS, fake.provider())
    score_candidate(job, profile_with_role(), DOCS, fake.provider(), variant=1)
    first, second = (str(c[2][-1].content) for c in fake.calls)
    assert first != second and '"assessment_pass": 2' in second
    # Documents and lists are presented in reverse order.
    assert first.index('type="cv"') < first.index('type="cover_letter"')
    assert second.index('type="cv"') > second.index('type="cover_letter"')


def test_experience_helpers() -> None:
    assert target_years(make_job(target_years=None, seniority="Senior")) == 6.0
    assert target_years(make_job(target_years=None, title="Junior developer")) == 1.0
    assert target_years(make_job(target_years=None, title="Analyst", seniority=None)) == 4.0
    assert [recency_value(y) for y in (None, 0, 1, 3.5, 6, 10)] == [0.0, 1.0, 1.0, 0.5, 0.0, 0.0]
    assert shrink(1.0, "high") == 1.0 and shrink(1.0, "low") == pytest.approx(0.8)
    assert shrink(0.0, "medium") == pytest.approx(0.075)


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


def test_combine_passes_averages_and_records_spread() -> None:
    job = make_job(weights={Dimension.EXPERIENCE: 1.0})
    a = card("a", {Dimension.EXPERIENCE: 0.8}, [RequirementStatus.FAIL])
    b = card("a", {Dimension.EXPERIENCE: 0.6}, [RequirementStatus.PASS])
    combined = combine_passes([a, b], job)
    exp = combined.score(Dimension.EXPERIENCE)
    assert exp is not None and exp.score == 0.7 and combined.spread == 0.2
    # The readings disagree on a must-have failing: unclear, for a person to check.
    a.requirement_checks[0].must_have = True
    combined = combine_passes([a, b], job)
    assert combined.requirement_checks[0].status is RequirementStatus.UNCLEAR
    assert combine_passes([a], job).spread is None


def test_tiers_group_totals_too_close_to_call() -> None:
    cards = [card(c, {}, []) for c in "abcde"]
    for c, total, spread in zip(
        cards, [0.80, 0.78, 0.70, 0.60, 0.50], [None, None, 0.0, 0.3, 0.0], strict=True
    ):
        c.total, c.spread = total, spread
    cards[4].flags = [Flag.KNOCKOUT_FAILED]
    assign_tiers(cards, margin=0.04)
    # 0.80/0.78 within the margin; 0.70 apart; 0.60 within the 0.15 mean spread of 0.70/0.60.
    assert [c.tier for c in cards] == [1, 1, 2, 2, None]


def test_judge_swaps_neighbours_only_when_both_orders_agree() -> None:
    def judge(messages: Any) -> dict[str, Any]:
        text = str(messages[-1].content)
        a = text.split('<candidate id="A">')[1].split("</candidate>")[0]
        # Prefers "strong" over anyone; between the others it always answers "A"
        # (position bias), which the second order cancels out.
        if "strong" in a:
            return {"better": "A", "rationale": "Stronger evidence."}
        if "strong" in text:
            return {"better": "B", "rationale": "Stronger evidence."}
        return {"better": "A", "rationale": "First seems fine."}

    fake = FakeLLM({PairwiseDraft: judge})
    cards = [card(c, {}, []) for c in ["upper", "strong", "third"]]
    for c, total in zip(cards, [0.80, 0.79, 0.78], strict=True):
        c.total, c.tier = total, 1
    docs = {c.candidate_id: {DocType.CV: f"CV of {c.candidate_id}"} for c in cards}
    ordered = judge_ties(cards, make_job(), docs, fake.provider(), top=10)
    assert [c.candidate_id for c in ordered] == ["strong", "upper", "third"]
    assert [c.rank for c in ordered] == [1, 2, 3]
    assert all(role is ModelRole.JUDGE for role, _, _ in fake.calls)
    assert len(fake.calls) == 4  # two neighbour pairs, both orders
    assert any("moved up" in n for n in ordered[0].notes)
    # strong vs third: preferred in both orders, confirmed (no move needed).
    assert any("confirmed this candidate over third" in n for n in ordered[0].notes)
    # Different tiers are never compared.
    cards[2].tier = 2
    fake.calls.clear()
    judge_ties(cards, make_job(), docs, fake.provider(), top=10)
    assert len(fake.calls) == 2


def test_judge_position_bias_does_not_move_anyone() -> None:
    fake = FakeLLM({PairwiseDraft: lambda _: {"better": "A", "rationale": "First one."}})
    cards = [card(c, {}, []) for c in ["one", "two"]]
    for c in cards:
        c.total, c.tier = 0.7, 1
    docs = {c.candidate_id: {DocType.CV: c.candidate_id} for c in cards}
    ordered = judge_ties(cards, make_job(), docs, fake.provider(), top=10)
    assert [c.candidate_id for c in ordered] == ["one", "two"]
    assert any("could not separate" in n for n in ordered[0].notes)
