"""Runs a whole assessment: the steps shared by ``cvscore run`` and the web app."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cvscore.config import Settings
from cvscore.graph import build_graph
from cvscore.ingest.pipeline import IngestResult
from cvscore.job import save_job_profile
from cvscore.llm.structured import LLMProvider
from cvscore.models import CandidateProfile, JobProfile, ScoreCard
from cvscore.observability import Tracer
from cvscore.redact import redact
from cvscore.report import write_report

Progress = Callable[[int, int], None]  # (candidates assessed, total)


def timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


@dataclass
class Outcome:
    run_id: str
    ranked: list[ScoreCard]
    profiles: list[CandidateProfile]


def assess_all(
    job: JobProfile,
    ingested: IngestResult,
    llm: LLMProvider,
    settings: Settings,
    *,
    run_id: str | None = None,
    on_progress: Progress | None = None,
) -> Outcome:
    """Assess and rank every candidate, reporting progress after each one."""
    run_id = run_id or f"{job.id}-{timestamp()}"
    tracer = Tracer.from_config(settings.langfuse)
    run_config = tracer.run_config(
        run_id=run_id,
        trace_name="assess-candidates",
        tags=[f"job:{job.id}"],
        max_concurrency=settings.max_concurrency,
    )
    total = len(ingested.candidates)
    done = 0
    ranked: list[ScoreCard] = []
    profiles: list[CandidateProfile] = []
    if on_progress:
        on_progress(0, total)
    graph = build_graph(llm, settings.ranking)
    state = {
        "job": job,
        "candidates": ingested.candidates,
        "identity": ingested.identity,
        "names": ingested.names,
    }
    try:
        for update in graph.stream(state, config=run_config, stream_mode="updates"):
            for node, value in update.items():
                if node == "assess" and value:
                    profiles.extend(a["profile"] for a in value.get("assessments", []))
                    done += len(value.get("assessments", []))
                    if on_progress:
                        on_progress(done, total)
                elif node == "rank" and value:
                    ranked = value.get("ranked", [])
    finally:
        tracer.flush()
    return Outcome(run_id=run_id, ranked=ranked, profiles=profiles)


def write_outputs(
    out: Path,
    job: JobProfile,
    ingested: IngestResult,
    outcome: Outcome,
    *,
    reveal_names: bool = False,
) -> None:
    """report.html, ranking.csv, results.json, plus the inputs a reviewer needs to check
    the result: the job profile, the redacted text each model saw, and the identity map."""
    names = ingested.identity if reveal_names else None
    write_report(out, job, outcome.ranked, outcome.profiles, names)
    save_job_profile(job, out / "job.yaml")
    redacted = {
        c.candidate_id: {
            str(t): text for t, text in redact(c, ingested.names.get(c.candidate_id, [])).items()
        }
        for c in ingested.candidates
    }
    (out / "redacted.json").write_text(json.dumps(redacted, indent=2, ensure_ascii=False), "utf-8")
    (out / "identity_map.json").write_text(
        json.dumps(ingested.identity, indent=2, ensure_ascii=False), encoding="utf-8"
    )
