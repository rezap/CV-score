"""The web API end to end: browse, scan, draft a job profile, run, read results."""

import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from cvscore.config import Settings
from cvscore.extract import CandidateProfileDraft
from cvscore.ingest.ocr import PageTranscript
from cvscore.job import JobProfileDraft
from cvscore.llm.structured import LLMProvider
from cvscore.score import ScoringDraft
from cvscore.web.app import create_app
from tests.fakes import FakeLLM
from tests.ingest import builders
from tests.test_pipeline_e2e import JOB_AD, job_handler, profile_handler, scoring_handler


@pytest.fixture
def fake() -> FakeLLM:
    return FakeLLM(
        {
            JobProfileDraft: job_handler,
            CandidateProfileDraft: profile_handler,
            ScoringDraft: scoring_handler,
            PageTranscript: lambda _: {"text": ""},  # the test scan is a blank page
        }
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "cvscore.yaml").write_text("langfuse:\n  enabled: false\n")
    root = tmp_path / "home" / "applications"
    root.mkdir(parents=True)
    # A flat folder: everyone's files together.
    builders.two_column_cv_pdf(root / "Jane_Example_CV.pdf")
    builders.word_style_cv_docx(root / "Alex_Sample_CV.docx")
    builders.scanned_pdf(root / "Sam_Roe_CV.pdf")
    (root / "notes").mkdir()
    return root


@pytest.fixture
def client(root: Path, tmp_path: Path, fake: FakeLLM) -> TestClient:
    def provider(settings: Settings, cache_dir: Path | None) -> LLMProvider:
        return fake.provider()

    app = create_app(
        browse_root=root.parent,
        runs_dir=tmp_path / "runs",
        config=tmp_path / "cvscore.yaml",
        provider=provider,
        static_dir=None,
    )
    return TestClient(app)


def wait_for(client: TestClient, run_id: str) -> dict[str, Any]:
    for _ in range(200):
        data: dict[str, Any] = client.get(f"/api/runs/{run_id}").json()
        if data["meta"]["status"] in ("done", "failed"):
            return data
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_status_reports_missing_configuration(client: TestClient) -> None:
    status = client.get("/api/status").json()
    assert status["ready"] is False
    assert status["provider"] == "azure"
    assert "azure.endpoint is not set in the config." in status["problems"]


def test_browse(client: TestClient, root: Path) -> None:
    top = client.get("/api/fs").json()
    assert top["path"] == str(root.parent) and top["parent"] is None
    assert [(f["name"], f["documents"]) for f in top["folders"]] == [("applications", 3)]

    inside = client.get("/api/fs", params={"path": str(root)}).json()
    assert inside["parent"] == str(root.parent)
    assert inside["documents"] == ["Alex_Sample_CV.docx", "Jane_Example_CV.pdf", "Sam_Roe_CV.pdf"]
    assert [f["name"] for f in inside["folders"]] == ["notes"]


def test_browse_stays_under_root(client: TestClient, root: Path) -> None:
    assert client.get("/api/fs", params={"path": "/"}).status_code == 403
    assert client.get("/api/fs", params={"path": str(root / "..")}).status_code == 200
    assert client.get("/api/fs", params={"path": str(root / ".." / "..")}).status_code == 403
    assert client.get("/api/fs", params={"path": str(root / "missing")}).status_code == 404


def test_scan_groups_candidates(client: TestClient, root: Path) -> None:
    result = client.post("/api/scan", json={"folder": str(root)}).json()
    by_label = {c["label"]: c for c in result["candidates"]}
    assert set(by_label) == {"Jane Example", "Alex Sample", "Sam Roe"}
    [jane_cv] = by_label["Jane Example"]["files"]
    assert jane_cv["doc_type"] == "cv" and "file name" in jane_cv["reason"]
    [sam_cv] = by_label["Sam Roe"]["files"]
    assert sam_cv["quality"] < 0.5 and sam_cv["warnings"]

    overridden = client.post(
        "/api/scan",
        json={
            "folder": str(root),
            "overrides": {"Alex_Sample_CV.docx": "cover_letter"},
            "exclude": ["Sam_Roe_CV.pdf"],
        },
    ).json()
    files = {f["path"]: f["doc_type"] for c in overridden["candidates"] for f in c["files"]}
    assert files == {"Alex_Sample_CV.docx": "cover_letter", "Jane_Example_CV.pdf": "cv"}


def test_job_from_ad_upload_text_and_yaml(client: TestClient, fake: FakeLLM) -> None:
    drafted = client.post("/api/job", files={"file": ("ad.txt", JOB_AD.encode(), "text/plain")})
    assert drafted.status_code == 200, drafted.text
    profile = drafted.json()["profile"]
    assert drafted.json()["source"] == "drafted"
    assert profile["title"] == "Senior Data Engineer"
    assert "streaming data platform" in str(fake.calls[-1][2][-1].content)

    pasted = client.post("/api/job", data={"text": JOB_AD})
    assert pasted.json()["profile"]["id"] == "senior-data-engineer"

    profile["title"] = "Edited title"
    loaded = client.post(
        "/api/job", files={"file": ("job.yaml", yaml.safe_dump(profile).encode(), "text/yaml")}
    )
    assert loaded.json() == {"profile": profile, "source": "loaded"}

    assert client.post("/api/job").status_code == 422
    bad = client.post("/api/job", files={"file": ("ad.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 415


def test_run_end_to_end(client: TestClient, root: Path, fake: FakeLLM) -> None:
    profile = client.post("/api/job", data={"text": JOB_AD}).json()["profile"]
    started = client.post("/api/runs", json={"folder": str(root), "job": profile})
    assert started.status_code == 200, started.text
    run_id = started.json()["id"]

    data = wait_for(client, run_id)
    assert data["meta"]["status"] == "done", data["meta"]
    assert data["meta"]["done"] == data["meta"]["total"] == 3
    ranking = [
        data["identity"][c["score_card"]["candidate_id"]] for c in data["results"]["candidates"]
    ]
    assert ranking == ["Jane Example", "Alex Sample", "Sam Roe"]
    assert {c["label"] for c in data["candidates"]} == set(ranking)

    jane_id = data["results"]["candidates"][0]["score_card"]["candidate_id"]
    redacted = client.get(f"/api/runs/{run_id}/redacted/{jane_id}").json()
    assert "[REDACTED: contact details]" in redacted["cv"]
    assert "Jane" not in redacted["cv"] and "jane@example.com" not in redacted["cv"]
    assert "Jane" not in fake.prompts()

    report = client.get(f"/api/runs/{run_id}/files/report.html")
    assert report.status_code == 200 and "Jane Example" not in report.text
    assert client.get(f"/api/runs/{run_id}/files/ranking.csv").status_code == 200
    assert client.get(f"/api/runs/{run_id}/files/secrets.txt").status_code == 404
    assert client.get("/api/runs/../files/report.html").status_code == 404
    assert [r["id"] for r in client.get("/api/runs").json()] == [run_id]


def test_run_failure_is_reported(client: TestClient, root: Path) -> None:
    profile = client.post("/api/job", data={"text": JOB_AD}).json()["profile"]
    empty = root / "notes"
    data = wait_for(
        client, client.post("/api/runs", json={"folder": str(empty), "job": profile}).json()["id"]
    )
    assert data["meta"]["status"] == "failed"
    assert "No candidate documents" in data["meta"]["error"]


def test_run_outside_root_is_refused(client: TestClient, root: Path) -> None:
    profile = client.post("/api/job", data={"text": JOB_AD}).json()["profile"]
    response = client.post("/api/runs", json={"folder": "/", "job": profile})
    assert response.status_code == 403


def test_scan_reads_scanned_cvs_with_ocr(client: TestClient, root: Path, fake: FakeLLM) -> None:
    fake.handlers[PageTranscript] = lambda _: {
        "text": "Sam Roe\nsam.roe@example.com\n\n## Experience\n"
        "- Data Engineer, Initech, 2018 - 2024"
    }
    result = client.post("/api/scan", json={"folder": str(root)}).json()
    [sam] = [c for c in result["candidates"] if c["label"] == "Sam Roe"]
    [cv] = sam["files"]
    assert cv["doc_type"] == "cv" and cv["names"] == ["Sam Roe"]
    assert any(w.startswith("Read by OCR") for w in cv["warnings"])
    assert any(schema is PageTranscript for _, schema, _ in fake.calls)
