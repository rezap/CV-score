"""The evaluation harness, run on the bundled set with a rule-based fake model.

The fake reads the redacted documents the way a careful scorer would (titles for
relevance, bullets for work, the skills line for listings), so the test checks that the
set, the scoring logic and the metrics fit together: e.g. the CV with Terraform in its
work history beats the same CV with Terraform only listed.
"""

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage

from cvscore.config import Settings
from cvscore.evaluate import Expected, kendall_tau, run_eval, score_report
from cvscore.extract import CandidateProfileDraft
from cvscore.judge import PairwiseDraft
from cvscore.score import ScoringDraft
from tests.fakes import FakeLLM, documents

SET = Path(__file__).parent.parent / "evals" / "cloud-architect"
ROLE = re.compile(r"^(?P<title>[^,]+), (?P<org>[^,]+), (?P<start>\d{4}) - (?P<end>\d{4}|present)$")
EQUIVALENTS = {
    "Terraform": ["Terraform", "Bicep", "ARM template"],
    "Kubernetes": ["Kubernetes", "AKS", "EKS"],
}


def cv_lines(messages: Sequence[BaseMessage]) -> list[str]:
    """The CV's lines, keeping "- " bullet markers (work items) apart from plain lines."""
    return [line.strip() for line in documents(messages)["cv"].splitlines() if line.strip()]


def bullets(cv: list[str]) -> list[str]:
    return [line.lstrip("- ").strip() for line in cv if line.startswith("- ")]


def profile_handler(messages: Sequence[BaseMessage]) -> dict[str, Any]:
    cv = cv_lines(messages)
    experience = []
    for line in cv:
        if m := ROLE.match(line.lstrip("#* ")):
            experience.append(
                {
                    "title": m["title"], "organisation": m["org"], "start": m["start"],
                    "end": None if m["end"] == "present" else m["end"],
                    "is_current": m["end"] == "present", "seniority": None, "domain": None,
                    "responsibilities": [], "technologies": [],
                    "evidence_doc": "cv", "evidence_quote": line.lstrip("#* "),
                }
            )  # fmt: skip
    return {
        "experience": experience, "achievements": [], "skills": [], "education": [],
        "certifications": [], "languages": [], "trait_evidence": [],
    }  # fmt: skip


def level_of(terms: list[str], cv: list[str]) -> tuple[str, list[dict[str, str]]]:
    def has(line: str) -> bool:
        return any(re.search(rf"\b{re.escape(t)}", line) for t in terms)

    work = [b for b in bullets(cv) if has(b)]
    if work:
        proven = [b for b in work if re.search(r"\d|Led|Designed|own", b)]
        quote = (proven or work)[0]
        return ("proven" if proven else "applied"), [{"doc": "cv", "quote": quote}]
    listed = [line for line in cv if has(line) and "," in line and not line.startswith("- ")]
    if listed:
        return "mentioned", [{"doc": "cv", "quote": listed[0]}]
    return "none", []


def scoring_handler(messages: Sequence[BaseMessage]) -> dict[str, Any]:
    cv = cv_lines(messages)
    titles = " ".join(line for line in cv if ROLE.match(line.lstrip("#* ")))
    relevance = (
        5
        if "Cloud" in titles or "Solution Architect" in titles
        else (3 if re.search(r"Engineer|Developer|Architect", titles) else 1)
    )
    seniority = (
        5
        if re.search(r"Principal|Senior.*Architect", titles)
        else (3 if "Senior" in titles or "Architect" in titles else 1)
    )
    requirement_terms = {
        "M1": ["AWS", "Azure", "GCP"],
        "M2": EQUIVALENTS["Terraform"],
        "M3": ["Principal", "Senior Solution Architect", "Infrastructure Architect"],
        "N1": EQUIVALENTS["Kubernetes"],
        "N2": ["ISO 27001", "security"],
        "N3": ["Certified"],
    }
    requirements = []
    for rid, terms in requirement_terms.items():
        level, quotes = level_of(terms, cv)
        if rid == "M3":  # seniority shows in the titles
            hit = [line for line in cv if any(t in line for t in terms) and ROLE.match(line)]
            level, quotes = ("proven", [{"doc": "cv", "quote": hit[0]}]) if hit else ("none", [])
        if rid == "N3":
            hit = [line for line in cv if "Certified" in line]
            level, quotes = ("applied", [{"doc": "cv", "quote": hit[0]}]) if hit else ("none", [])
        requirements.append(
            {"requirement_id": rid, "level": level, "contradicted": False, "rationale": "Rule.",
             "quotes": quotes}
        )  # fmt: skip
    skills = []
    for skill in ["Terraform", "AWS", "Kubernetes", "CI/CD", "Python"]:
        level, quotes = level_of(EQUIVALENTS.get(skill, [skill]), cv)
        skills.append({"skill": skill, "level": level, "quotes": quotes})
    roles = [i for i, line in enumerate(line for line in cv if ROLE.match(line.lstrip("#* ")))]
    relevant = roles if relevance >= 3 else []
    work = bullets(cv)
    quote = [{"doc": "cv", "quote": work[0]}] if work else []
    numbers = [b for b in work if re.search(r"\d", b)]
    return {
        "requirements": requirements,
        "skills": skills,
        "experience": {
            "relevant_roles": relevant, "relevance": relevance, "seniority": seniority,
            "confidence": "high", "rationale": "Rule.", "quotes": quote,
        },
        "achievements": {
            "score": min(5, 1 + len(numbers)), "confidence": "high", "rationale": "Rule.",
            "quotes": [{"doc": "cv", "quote": numbers[0]}] if numbers else [],
        },
        "traits": [],
    }  # fmt: skip


def test_eval_set_ranks_as_expected_with_a_careful_scorer() -> None:
    fake = FakeLLM(
        {
            CandidateProfileDraft: profile_handler,
            ScoringDraft: scoring_handler,
            PairwiseDraft: lambda _: {"better": "tie", "rationale": "Close."},
        }
    )
    settings = Settings(langfuse={"enabled": False})  # type: ignore[arg-type]
    report = run_eval(SET, settings, lambda _: fake.provider(), repeats=2)

    expected = Expected.model_validate(
        __import__("yaml").safe_load((SET / "expected.yaml").read_text())
    )
    # Every candidate in the set was found under the right name (one file each).
    assert set(report.candidates) == set(expected.tiers)
    assert report.repeats == 2
    assert all(c.mean is not None for c in report.candidates.values())
    assert report.tier_order_accuracy == 1.0, report.summary()
    assert report.kendall_tau is not None and report.kendall_tau > 0.8, report.summary()
    assert all(p.ok for p in report.better), report.summary()
    # Terraform used in work, not only listed, is worth a clear margin (requirement M2
    # and the Terraform key skill both get full instead of half credit).
    kofi_vs_tomasz = next(p for p in report.better if p.a == "Kofi Mensah")
    assert kofi_vs_tomasz.difference is not None and kofi_vs_tomasz.difference >= 0.04
    assert all(p.ok for p in report.same), report.summary()
    # The fake is deterministic, so totals do not move between runs.
    assert report.max_spread == 0.0
    assert "tier order accuracy 100%" in report.summary()


def test_metrics() -> None:
    assert kendall_tau([1, 2, 3], [1, 2, 3]) == 1.0
    assert kendall_tau([1, 2, 3], [3, 2, 1]) == -1.0
    assert kendall_tau([1, 1], [1, 1]) is None
    expected = Expected(
        tiers={"a": 1, "b": 2, "c": 2}, better=[("a", "b", "")], same=[("b", "c", "")]
    )
    report = score_report(
        "Job",
        {"a": [0.8, 0.7], "b": [0.75, 0.6], "c": [0.66, 0.7]},
        {"a": [1, 1], "b": [2, 3], "c": [3, 2]},
        expected,
    )
    assert report.candidates["a"].mean == 0.75 and report.candidates["a"].spread == 0.1
    assert report.tier_order_accuracy == 1.0
    assert report.better[0].ok and report.better[0].difference == 0.075
    assert report.same[0].ok and report.max_spread == 0.15


def test_eval_command(tmp_path: Path, monkeypatch: Any) -> None:
    from typer.testing import CliRunner

    from cvscore import cli

    fake = FakeLLM(
        {
            CandidateProfileDraft: profile_handler,
            ScoringDraft: scoring_handler,
            PairwiseDraft: lambda _: {"better": "tie", "rationale": "Close."},
        }
    )
    monkeypatch.setattr(cli, "llm_provider", lambda settings, cache_dir: fake.provider())
    (tmp_path / "cvscore.yaml").write_text("langfuse:\n  enabled: false\n")
    result = CliRunner().invoke(
        cli.app,
        ["eval", str(SET), "--repeats", "1", "--out", str(tmp_path / "out"),
         "--config", str(tmp_path / "cvscore.yaml")],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "tier order accuracy 100%" in result.output
    assert (tmp_path / "out" / "eval.json").is_file()
