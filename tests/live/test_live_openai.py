"""Live end-to-end test against a real OpenAI model. Opt-in; costs a few cents per run.

Runs only with ``CVSCORE_LIVE=1`` and ``OPENAI_API_KEY`` set, e.g.::

    CVSCORE_LIVE=1 OPENAI_API_KEY=sk-... uv run pytest tests/live -v -s

Optional: ``CVSCORE_LIVE_MODEL`` (default gpt-4.1-mini) and ``CVSCORE_LIVE_OUT`` (folder to
keep job.yaml and the report in; the CI workflow uploads it).

Model output varies, so the assertions check what a working system must get right
regardless of wording: the obviously strongest candidate ranks first, the unrelated one
ranks below every relevant one, the scanned CV is not sent to the model, every judgement
is backed by verified quotes, and no personal data appears in any prompt.
"""

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from typer.testing import CliRunner

from cvscore import cli
from cvscore.config import Settings
from cvscore.llm.structured import LLMProvider, StructuredLLM, T, settings_provider
from tests.conftest import ORIGINAL_CWD, ORIGINAL_ENV
from tests.ingest import builders

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("CVSCORE_LIVE") == "1" and os.environ.get("OPENAI_API_KEY")),
        reason="live test: set CVSCORE_LIVE=1 and OPENAI_API_KEY",
    ),
]

JOB_AD = """\
Senior Data Engineer

We are looking for a Senior Data Engineer to design, build and run our streaming data
platform, and to mentor a small team of engineers.

What you will do
- Build and operate batch and streaming pipelines (Spark, Kafka, Airflow).
- Own data quality: monitoring, alerting and incident response.
- Mentor engineers and review their designs and code.

Requirements
- At least 5 years of professional data engineering experience.
- Strong Python and SQL.
- Experience leading or mentoring engineers.

Nice to have
- Apache Spark and Kafka in production.
- dbt.

We value people who share knowledge and take ownership.
"""

SECRETS = [
    "Jane Example", "jane@example.com", "7946 0958",
    "Alex Sample", "alex.sample@example.org", "1234 5678",
    "Morgan Lee", "morgan.lee@example.net", "496 0000",
]  # fmt: skip


class RecordingLLM:
    """Wraps a real model and records every prompt it is sent."""

    def __init__(self, inner: StructuredLLM, prompts: list[str]) -> None:
        self.inner = inner
        self.model_id = inner.model_id
        self.prompts = prompts

    def generate(
        self, schema: type[T], messages: Sequence[BaseMessage], config: RunnableConfig | None
    ) -> T:
        self.prompts.extend(str(m.content) for m in messages)
        return self.inner.generate(schema, messages, config)


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPENAI_API_KEY", ORIGINAL_ENV["OPENAI_API_KEY"])
    model = ORIGINAL_ENV.get("CVSCORE_LIVE_MODEL") or "gpt-4.1-mini"
    (tmp_path / "cvscore.yaml").write_text(
        f"provider: openai\nopenai:\n  models:\n    scorer:\n      model: {model}\n"
        "  max_concurrency: 4\nlangfuse:\n  enabled: false\n"
    )
    (tmp_path / "job_ad.txt").write_text(JOB_AD)
    root = tmp_path / "candidates"
    for name in ["Jane Example", "Alex Sample", "Morgan Lee", "Sam Roe"]:
        (root / name).mkdir(parents=True)
    builders.two_column_cv_pdf(root / "Jane Example" / "cv.pdf")
    builders.single_column_pdf(root / "Jane Example" / "cover_letter.pdf")
    builders.word_style_cv_docx(root / "Alex Sample" / "cv.docx")
    builders.marketing_cv_docx(root / "Morgan Lee" / "cv.docx")
    builders.scanned_pdf(root / "Sam Roe" / "resume.pdf")
    return tmp_path


def invoke(*args: str) -> str:
    result = CliRunner().invoke(cli.app, list(args))
    print(result.output)
    assert result.exit_code == 0, result.output
    return result.output


def test_live_end_to_end(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def provider(settings: Settings, cache_dir: Path | None) -> LLMProvider:
        real = settings_provider(settings, cache_dir=cache_dir)
        return lambda role: RecordingLLM(real(role), prompts)

    monkeypatch.setattr(cli, "llm_provider", provider)
    keep = ORIGINAL_ENV.get("CVSCORE_LIVE_OUT")
    # Relative to where pytest was started (tests run inside a temporary directory).
    out = (ORIGINAL_CWD / keep).resolve() if keep else workdir / "out"
    out.mkdir(parents=True, exist_ok=True)

    invoke("doctor")
    invoke("job", "draft", "job_ad.txt", "--out", str(out / "job.yaml"))
    job_yaml = (out / "job.yaml").read_text()
    assert "must_have:\n- id: M1" in job_yaml, job_yaml
    assert "key_skills:\n- " in job_yaml, job_yaml

    invoke(
        "run", "--job", str(out / "job.yaml"), "--candidates", "candidates",
        "--out", str(out), "--no-cache",
    )  # fmt: skip

    identity = json.loads((out / "identity_map.json").read_text())
    results = json.loads((out / "results.json").read_text())
    cards = {
        identity[c["score_card"]["candidate_id"]]: c["score_card"] for c in results["candidates"]
    }
    totals = {name: card["total"] for name, card in cards.items()}
    print("totals:", totals)

    # Unreadable CV: never sent to the model, flagged, no score.
    assert cards["Sam Roe"]["total"] is None
    assert "needs_review" in cards["Sam Roe"]["flags"]

    # The obvious ranking holds.
    assert cards["Jane Example"]["rank"] == 1, totals
    assert totals["Morgan Lee"] is not None
    assert totals["Morgan Lee"] < totals["Alex Sample"] < totals["Jane Example"], totals

    # Scores for a real candidate are backed by quotes that were verified against the CV.
    jane = cards["Jane Example"]
    experience = next(s for s in jane["scores"] if s["dimension"] == "experience")
    assert experience["evidence"], experience
    passed = [c for c in jane["requirement_checks"] if c["status"] == "pass"]
    assert passed and all(c["evidence"] for c in passed), jane["requirement_checks"]

    # Nothing identifying reached the model; the scanned CV was never sent.
    sent = "\n".join(prompts)
    leaked = [s for s in SECRETS if s.lower() in sent.lower()]
    assert not leaked, leaked
    candidate_calls = sum("<document type=" in p for p in prompts)
    assert candidate_calls == 6  # 3 readable candidates x (extraction + scoring)

    assert (out / "report.html").is_file()
