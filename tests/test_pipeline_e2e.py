"""End to end: `cvscore job draft` and `cvscore run` on real ingested files, fake model."""

import csv
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import BaseMessage
from typer.testing import CliRunner

from cvscore import cli
from cvscore.config import Settings
from cvscore.extract import CandidateProfileDraft
from cvscore.ingest.ocr import PageTranscript
from cvscore.job import JobProfileDraft
from cvscore.llm.structured import CachedLLM, LLMProvider
from cvscore.score import ScoringDraft
from tests.fakes import FakeLLM, documents, lines
from tests.ingest import builders

JOB_AD = """Senior Data Engineer (m/f/d)

You will build and run our streaming data platform and mentor a small team.

Requirements: 5+ years of data engineering experience; Python and SQL.
Nice to have: Apache Spark, Kafka. We value people who share knowledge.
"""


def job_handler(_: Sequence[BaseMessage]) -> dict[str, Any]:
    return {
        "title": "Senior Data Engineer",
        "seniority": "senior",
        "domain": "data",
        "summary": "Build and run the streaming data platform.",
        "responsibilities": ["Build the streaming platform", "Mentor engineers"],
        "must_have": [{"description": "5+ years of data engineering experience"}],
        "nice_to_have": [{"description": "Apache Spark", "importance": "medium"}],
        "key_skills": ["Python", "SQL", "Apache Spark", "Kafka"],
        "traits": [
            {"name": "Knowledge sharing", "definition": "Teaches others.", "evidence_examples": []}
        ],
        "screening": [
            {
                "name": "Data programming",
                "terms": ["Python", "SQL", "Scala"],
                "required": True,
                "importance": "high",
            },
            {
                "name": "Streaming and big data",
                "terms": ["Spark", "Kafka", "Flink", "streaming"],
                "required": False,
                "importance": "medium",
            },
        ],
    }


def profile_handler(messages: Sequence[BaseMessage]) -> dict[str, Any]:
    cv = lines(documents(messages)["cv"])
    role = next(line for line in cv if "Engineer" in line or "Scientist" in line)
    achievement = next(line for line in cv if "%" in line)
    return {
        "experience": [
            {
                "title": role.split(",")[0],
                "organisation": None,
                "start": "2016",
                "end": None,
                "is_current": True,
                "seniority": None,
                "domain": None,
                "responsibilities": [],
                "technologies": ["Python"],
                "evidence_doc": "cv",
                "evidence_quote": role,
            }
        ],
        "achievements": [
            {
                "statement": achievement,
                "metric": None,
                "experience_index": 0,
                "evidence_doc": "cv",
                "evidence_quote": achievement,
            }
        ],
        "skills": ["Python", "SQL"],
        "education": [],
        "certifications": [],
        "languages": [],
        "trait_evidence": [],
    }


def scoring_handler(messages: Sequence[BaseMessage]) -> dict[str, Any]:
    cv_text = documents(messages)["cv"]
    quote = {"doc": "cv", "quote": next(line for line in lines(cv_text) if "%" in line)}
    data_engineer = "Data Engineer" in cv_text
    dim = {"confidence": "high", "rationale": "From the CV.", "quotes": [quote]}
    return {
        "requirements": [
            {
                "requirement_id": "M1",
                "status": "pass" if data_engineer else "fail",
                "rationale": "Role history.",
                "quotes": [quote],
            }
        ],
        "experience": {**dim, "score": 5 if data_engineer else 2},
        "achievements": {**dim, "score": 4},
        "skills": {**dim, "score": 4 if data_engineer else 2},
        "traits": [{"trait_id": "T1", "score": None, "rationale": "No evidence.", "quotes": []}],
    }


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    llm = FakeLLM(
        {
            JobProfileDraft: job_handler,
            CandidateProfileDraft: profile_handler,
            ScoringDraft: scoring_handler,
            PageTranscript: lambda _: {"text": ""},  # the scan is a blank image
        }
    )

    def provider(settings: Settings, cache_dir: Path | None) -> LLMProvider:
        if cache_dir is None:
            return llm.provider()
        return lambda role: CachedLLM(llm.provider()(role), cache_dir)

    monkeypatch.setattr(cli, "llm_provider", provider)
    return llm


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "cvscore.yaml").write_text("langfuse:\n  enabled: false\n")
    ad = tmp_path / "job_ad.txt"
    ad.write_text(JOB_AD)
    root = tmp_path / "candidates"
    (root / "Jane Example").mkdir(parents=True)
    builders.two_column_cv_pdf(root / "Jane Example" / "cv.pdf")
    builders.single_column_pdf(root / "Jane Example" / "cover_letter.pdf")
    (root / "Alex Sample").mkdir()
    builders.word_style_cv_docx(root / "Alex Sample" / "Alex_Sample_CV.docx")
    (root / "Sam Roe").mkdir()
    builders.scanned_pdf(root / "Sam Roe" / "resume.pdf")
    return ad, root


def run(*args: str) -> str:
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_draft_then_run(fake: FakeLLM, setup: tuple[Path, Path], tmp_path: Path) -> None:
    ad, root = setup
    out = tmp_path / "out"

    output = run("job", "draft", str(ad), "--out", str(tmp_path / "job.yaml"))
    assert "1 must-haves" in output
    assert "Senior Data Engineer" in (tmp_path / "job.yaml").read_text()

    output = run("run", "--job", "job.yaml", "--candidates", str(root), "--out", str(out))
    assert "Assessing 3 candidates" in output

    with (out / "ranking.csv").open() as f:
        rows = list(csv.DictReader(f))
    identity = json.loads((out / "identity_map.json").read_text())
    names = [identity[r["candidate"]] for r in rows]
    assert names == ["Jane Example", "Alex Sample", "Sam Roe"]
    jane, alex, sam = rows
    assert jane["flags"] == "" and float(jane["total"]) > float(alex["total"])
    assert "knockout_failed" in alex["flags"]  # must-have failed: ranked after Jane
    assert sam["total"] == "" and "needs_review" in sam["flags"]
    assert "No readable text" in sam["notes"]

    # The scanned CV was never sent to the model; the other two were, once per step.
    schemas = [schema for _, schema, _ in fake.calls]
    assert schemas.count(CandidateProfileDraft) == 2
    assert schemas.count(ScoringDraft) == 2

    # Nothing identifying reached the model.
    prompts = fake.prompts()
    for secret in ["jane@example.com", "7946 0958", "Jane", "alex.sample@", "1234 5678", "Alex"]:
        assert secret not in prompts, secret

    report = (out / "report.html").read_text()
    assert "Jane Example" not in report and "c-" in report
    results = json.loads((out / "results.json").read_text())
    assert results["job"]["id"] == "senior-data-engineer"
    assert results["candidates"][0]["profile"]["achievements"]

    # A second run with --reveal-names is served from the cache: no new model calls.
    calls = len(fake.calls)
    run("run", "--job", "job.yaml", "--candidates", str(root), "--out", str(out), "--reveal-names")
    assert len(fake.calls) == calls
    assert "Jane Example (c-" in (out / "report.html").read_text()


def test_run_without_candidates_fails(
    fake: FakeLLM, setup: tuple[Path, Path], tmp_path: Path
) -> None:
    ad, root = setup
    run("job", "draft", str(ad), "--out", "job.yaml")
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliRunner().invoke(cli.app, ["run", "--job", "job.yaml", "--candidates", str(empty)])
    assert result.exit_code == 2
    shutil.rmtree(empty)


def test_screen_is_fast_and_uses_no_chat_model(
    fake: FakeLLM, setup: tuple[Path, Path], tmp_path: Path
) -> None:
    ad, root = setup
    run("job", "draft", str(ad), "--out", "job.yaml")
    calls = len(fake.calls)
    output = run("screen", "--job", "job.yaml", "--candidates", str(root), "--out", "screen")
    assert "Screened 3 candidates for 'Senior Data Engineer' (keywords)" in output
    # Only OCR of the (blank) scanned CV; no chat model reads the CVs.
    schemas = [schema for _, schema, _ in fake.calls[calls:]]
    assert schemas == [PageTranscript]

    data = json.loads((tmp_path / "screen" / "screening.json").read_text())
    identity = json.loads((tmp_path / "screen" / "identity_map.json").read_text())
    ranked = [(identity[c["candidate_id"]], c["screened_out"]) for c in data["candidates"]]
    assert ranked[0] == ("Jane Example", False)
    assert ranked[-1] == ("Sam Roe", False)  # no readable text: unscored, listed last
    jane = data["candidates"][0]
    assert "Python" in jane["criteria"][0]["matched"]
    report = (tmp_path / "screen" / "screening.html").read_text()
    assert "Data programming (required)" in report and "Jane Example" not in report


def test_run_with_shortlist_assesses_only_the_best(
    fake: FakeLLM, setup: tuple[Path, Path], tmp_path: Path
) -> None:
    ad, root = setup
    run("job", "draft", str(ad), "--out", "job.yaml")
    out = tmp_path / "out"
    output = run(
        "run", "--job", "job.yaml", "--candidates", str(root), "--out", str(out),
        "--shortlist", "1",
    )  # fmt: skip
    assert "Assessing the best 1 of 3 candidates" in output
    schemas = [schema for _, schema, _ in fake.calls]
    assert schemas.count(CandidateProfileDraft) == 1 and schemas.count(ScoringDraft) == 1

    identity = json.loads((out / "identity_map.json").read_text())
    results = json.loads((out / "results.json").read_text())
    cards = [c["score_card"] for c in results["candidates"]]
    assert [identity[c["candidate_id"]] for c in cards][0] == "Jane Example"
    assert [c["rank"] for c in cards] == [1, 2, 3]
    rest = cards[1:]
    assert all(c["total"] is None and c["notes"][0].startswith("Screening only") for c in rest)
    assert (out / "screening.json").is_file()
