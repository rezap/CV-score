"""Fast screening: keyword and semantic matching, the required gate, years, ranking."""

import math
from datetime import date
from pathlib import Path

import pytest

from cvscore.config import ScreeningConfig
from cvscore.ingest.pipeline import IngestResult
from cvscore.models import (
    BlockKind,
    CandidateDocuments,
    DocType,
    JobProfile,
    ScreeningCriterion,
    SourceDocument,
    TextBlock,
)
from cvscore.screen import keyword_score, screen_all, screen_candidate, term_pattern, years_hint

CONFIG = ScreeningConfig()

JOB = JobProfile(
    id="senior-data-scientist",
    title="Senior Data Scientist",
    screening=[
        ScreeningCriterion(
            id="S1",
            name="Programming for data analysis",
            terms=["Python", "R", "pandas", "SQL"],
            required=True,
            weight=2,
        ),
        ScreeningCriterion(
            id="S2",
            name="Statistics",
            terms=["statistics", "statistikk", "regression", "time series"],
            weight=1,
        ),
        ScreeningCriterion(id="S3", name="Cloud ML", terms=["Azure ML", "SageMaker"], weight=1),
    ],
)


def candidate(cid: str, *lines: str, section: str = "Experience") -> CandidateDocuments:
    blocks = [TextBlock(kind=BlockKind.HEADING, text=section, section=section)]
    blocks += [TextBlock(kind=BlockKind.LIST_ITEM, text=t, section=section) for t in lines]
    doc = SourceDocument(
        doc_type=DocType.CV, path=f"{cid}.pdf", sha256="0", text="", blocks=blocks,
        extractor="test", quality=1,
    )  # fmt: skip
    return CandidateDocuments(candidate_id=cid, documents=[doc])


@pytest.mark.parametrize(
    ("term", "text", "found"),
    [
        ("Python", "Built pipelines in python and SQL.", True),
        ("Python", "Pythonic code", False),
        ("R", "Analyses in R and Python", True),
        ("R", "Led R&D projects", False),
        ("R", "[REDACTED: name] reviewed", False),
        ("R", "skills: r, python", False),  # one-letter terms match in the given case only
        ("C++", "C++ and Rust", True),
        ("time series", "time-series forecasting", True),
        ("time series", "timeseries", True),
        ("statistikk", "Master i Statistikk", True),
        ("Azure ML", "deployed on azure  ml", True),
        ("SQL", "NoSQL stores", False),
    ],
)
def test_term_pattern(term: str, text: str, found: bool) -> None:
    pattern = term_pattern(term)
    assert pattern is not None
    assert bool(pattern.search(text)) is found


def test_keyword_score() -> None:
    assert [keyword_score(n) for n in range(4)] == [0.0, 0.6, 0.84, 0.94]


def test_strong_candidate() -> None:
    r = screen_candidate(
        JOB,
        candidate(
            "c-1",
            "Senior Data Scientist, Equinor, 2019 - present: Python, pandas and SQL.",
            "Time-series forecasting and regression models on Azure ML.",
        ),
        [],
        CONFIG,
    )
    by_id = {c.id: c for c in r.criteria}
    assert by_id["S1"].matched == ["Python", "pandas", "SQL"]
    assert by_id["S2"].matched == ["regression", "time series"]
    assert by_id["S3"].matched == ["Azure ML"]
    assert "pandas and SQL" in by_id["S1"].snippets[0]
    assert not r.screened_out and r.score is not None and r.score > 0.8


def test_required_criterion_screens_out_and_says_why() -> None:
    r = screen_candidate(
        JOB, candidate("c-2", "Store manager, 2015 - 2023: sales and statistics reports."),
        [], CONFIG,
    )  # fmt: skip
    assert r.screened_out
    assert r.notes == [
        "No mention of required 'Programming for data analysis' (searched: Python, R, pandas, SQL)."
    ]


def test_names_are_redacted_before_matching() -> None:
    # A candidate called "Python" must not match the Python criterion by their name.
    cand = candidate("c-3", "Monty Python, office manager since 2010")
    r = screen_candidate(JOB, cand, ["Monty Python"], CONFIG)
    assert r.screened_out


def test_no_text() -> None:
    r = screen_candidate(JOB, candidate("c-4"), [], CONFIG)
    assert r.score is None and r.notes == ["No readable text; not screened."]


def fake_embedder(texts: list[str]) -> list[list[float]]:
    """2-d vectors: 'analysis'-like texts point one way, everything else another."""
    out = []
    for t in texts:
        data = any(w in t.lower() for w in ("programming", "scripts", "analysis"))
        angle = 0.0 if data else math.pi / 2
        out.append([math.cos(angle), math.sin(angle)])
    return out


def test_semantic_match_passes_the_gate_but_not_full_credit() -> None:
    cand = candidate("c-5", "Wrote scripts to automate the analysis of survey data")
    r = screen_candidate(JOB, cand, [], CONFIG, embed=fake_embedder)
    s1 = next(c for c in r.criteria if c.id == "S1")
    assert s1.matched == [] and s1.semantic_score == 1.0
    assert s1.score == 0.8  # capped: keywords are needed for full credit
    assert not r.screened_out


def test_semantic_failure_falls_back_to_keywords() -> None:
    def broken(_: list[str]) -> list[list[float]]:
        raise ConnectionError("no network")

    r = screen_candidate(
        JOB, candidate("c-6", "Python developer for ten years"), [], CONFIG, embed=broken
    )
    assert r.criteria[0].matched == ["Python"]
    assert "Semantic search failed (ConnectionError); keywords only." in r.notes


def test_years_hint_skips_education() -> None:
    blocks = [
        TextBlock(kind=BlockKind.PARAGRAPH, text="Analyst, 01.2015 - 12.2018", section="Work"),
        TextBlock(kind=BlockKind.PARAGRAPH, text="Lead, Mar 2018 - present", section="Work"),
        TextBlock(kind=BlockKind.PARAGRAPH, text="MSc, 2010 - 2015", section="Education"),
    ]
    assert years_hint(blocks, today=date(2024, 1, 1)) == 9.0


def test_screen_all_ranks_and_falls_back_to_key_skills() -> None:
    ingested = IngestResult(
        candidates=[
            candidate("weak", "Store manager, sales reports"),
            candidate("strong", "Python, pandas, SQL; regression; Azure ML"),
            candidate("empty"),
            candidate("medium", "Python scripts"),
        ]
    )
    ranked = screen_all(JOB, ingested, CONFIG)
    assert [(r.candidate_id, r.rank) for r in ranked] == [
        ("strong", 1), ("medium", 2), ("weak", 3), ("empty", 4),
    ]  # fmt: skip
    legacy = JOB.model_copy(update={"screening": [], "key_skills": ["Python", "Kafka"]})
    [r] = screen_all(legacy, IngestResult(candidates=[candidate("x", "python")]), CONFIG)
    assert [(c.name, c.matched) for c in r.criteria] == [("Python", ["Python"]), ("Kafka", [])]


def test_cached_embedder(tmp_path: Path) -> None:
    from cvscore.llm.embeddings import cached_embedder

    calls: list[list[str]] = []

    class Model:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            calls.append(texts)
            return [[float(len(t)), 1.0] for t in texts]

    embed = cached_embedder(Model(), "m", tmp_path)  # type: ignore[arg-type]
    assert embed(["ab", "abc"]) == [[2.0, 1.0], [3.0, 1.0]]
    assert embed(["abc", "abcd"]) == [[3.0, 1.0], [4.0, 1.0]]
    assert calls == [["ab", "abc"], ["abcd"]]  # only new texts are sent
