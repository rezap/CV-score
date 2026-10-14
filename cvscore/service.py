"""Runs a whole assessment: the steps shared by ``cvscore run`` and the web app."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from cvscore.config import Provider, Settings
from cvscore.graph import build_graph
from cvscore.ingest.pipeline import IngestResult
from cvscore.job import save_job_profile
from cvscore.llm.embeddings import Embedder, cached_embedder
from cvscore.llm.structured import LLMProvider
from cvscore.models import CandidateProfile, JobProfile, ScoreCard
from cvscore.observability import Tracer
from cvscore.prompts import PROMPT_VERSIONS
from cvscore.redact import redact
from cvscore.report import write_report, write_screening
from cvscore.screen import ScreenResult, screen_all

Progress = Callable[[int, int], None]  # (candidates assessed, total)


def timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


@dataclass
class Outcome:
    run_id: str
    ranked: list[ScoreCard]
    profiles: list[CandidateProfile]
    screening: list[ScreenResult] = field(default_factory=list)


def make_embedder(settings: Settings, cache_dir: Path | None) -> Embedder | None:
    """The embeddings model for semantic screening, or None (keywords only) when semantic
    screening is off or no embeddings model is configured."""
    if not settings.screening.semantic:
        return None
    from cvscore.llm.factory import ConfigurationError, embeddings_model

    try:
        model = embeddings_model(settings)
    except ConfigurationError:
        return None
    if settings.provider is Provider.OPENAI:
        name = settings.openai.embeddings.model if settings.openai.embeddings else "?"
    else:
        name = settings.azure.embeddings.deployment if settings.azure.embeddings else "?"
    return cached_embedder(model, f"{settings.provider}:{name}", cache_dir)


def screen(
    job: JobProfile, ingested: IngestResult, settings: Settings, embed: Embedder | None
) -> list[ScreenResult]:
    """Fast screening of every candidate (no chat model)."""
    return screen_all(job, ingested, settings.screening, embed)


def _not_shortlisted(job: JobProfile, r: ScreenResult, shortlist: int) -> ScoreCard:
    why = "screened out" if r.screened_out else f"not in the top {shortlist}"
    score = "n/a" if r.score is None else f"{round(r.score * 100)}"
    return ScoreCard(
        candidate_id=r.candidate_id,
        job_id=job.id,
        features={"screen_score": r.score},
        notes=[f"Screening only ({why}; screening score {score}, rank {r.rank}).", *r.notes],
        prompt_versions=dict(PROMPT_VERSIONS),
    )


def assess_all(
    job: JobProfile,
    ingested: IngestResult,
    llm: LLMProvider,
    settings: Settings,
    *,
    run_id: str | None = None,
    on_progress: Progress | None = None,
    shortlist: int | None = None,
    embed: Embedder | None = None,
) -> Outcome:
    """Screen every candidate, then assess and rank them in depth (only the best
    ``shortlist`` of the screening, if given), reporting progress after each one."""
    run_id = run_id or f"{job.id}-{timestamp()}"
    screening = screen(job, ingested, settings, embed)
    rest: list[ScoreCard] = []
    if shortlist is not None:
        chosen = [r for r in screening if not r.screened_out and r.score is not None]
        keep = {r.candidate_id for r in chosen[:shortlist]}
        rest = [
            _not_shortlisted(job, r, shortlist) for r in screening if r.candidate_id not in keep
        ]
        ingested = replace(
            ingested, candidates=[c for c in ingested.candidates if c.candidate_id in keep]
        )
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
        stream = (
            graph.stream(state, config=run_config, stream_mode="updates")
            if ingested.candidates
            else []
        )
        for update in stream:
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
    for i, card in enumerate(rest, len(ranked) + 1):
        card.rank = i
    return Outcome(run_id=run_id, ranked=[*ranked, *rest], profiles=profiles, screening=screening)


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
    if outcome.screening:
        write_screening(out, job, outcome.screening, names)
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
