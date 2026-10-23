"""Background assessment runs for the web app, stored on disk so they survive a restart.

Each run lives in ``<runs_dir>/<run_id>/``: ``meta.json`` (status and progress), the
report files written by ``service.write_outputs``, and ``candidates.json`` (how files
were grouped). Model results are cached in ``<runs_dir>/.cache`` across runs.
"""

from __future__ import annotations

import json
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from cvscore.config import Settings
from cvscore.ingest import ingest
from cvscore.ingest.ocr import ocr_hook
from cvscore.job import load_job_profile, save_job_profile
from cvscore.llm.structured import LLMProvider
from cvscore.models import DocType, JobProfile
from cvscore.report import write_screening
from cvscore.service import assess_all, make_embedder, screen, timestamp, write_outputs
from cvscore.web.schemas import CandidateSummary, summarize

Status = Literal["reading", "assessing", "done", "failed"]


class RunMeta(BaseModel):
    id: str
    status: Status
    mode: Literal["full", "screen"] = "full"
    shortlist: int | None = None
    created: str
    folder: str
    job_title: str
    total: int = 0
    done: int = 0
    error: str | None = None


class RunRequest(BaseModel):
    folder: str
    job: JobProfile
    overrides: dict[str, DocType] = {}
    exclude: list[str] = []
    # "screen": fast keyword/semantic screening only, no chat model reads the CVs.
    # "full": screening, then the in-depth assessment (of the best `shortlist`, if set).
    mode: Literal["full", "screen"] = "full"
    shortlist: int | None = Field(default=None, ge=1)


ProviderFactory = Callable[[Settings, Path | None], LLMProvider]


class RunManager:
    def __init__(
        self,
        runs_dir: Path,
        settings: Callable[[], Settings],
        provider: ProviderFactory,
        *,
        cache: bool = True,
    ) -> None:
        self.runs_dir = runs_dir
        self.settings = settings
        self.provider = provider
        self.cache = cache
        self._lock = threading.Lock()
        # Reading a folder parses every new file (seconds of CPU each for long PDFs).
        # Scans and runs take turns, so a second one finds the first one's files cached
        # instead of parsing them again alongside it.
        self.reading = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cvscore-run")

    # --- storage ---------------------------------------------------------------------

    def path(self, run_id: str) -> Path:
        path = (self.runs_dir / run_id).resolve()
        if path.parent != self.runs_dir.resolve() or not path.is_dir():
            raise KeyError(run_id)
        return path

    def _save(self, out: Path, meta: RunMeta) -> None:
        with self._lock:
            tmp = out / "meta.json.tmp"
            tmp.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(out / "meta.json")

    def meta(self, run_id: str) -> RunMeta:
        return RunMeta.model_validate_json((self.path(run_id) / "meta.json").read_text("utf-8"))

    def list_runs(self) -> list[RunMeta]:
        if not self.runs_dir.is_dir():
            return []
        metas = []
        for p in self.runs_dir.iterdir():
            if (p / "meta.json").is_file():
                try:
                    metas.append(RunMeta.model_validate_json((p / "meta.json").read_text("utf-8")))
                except ValueError:
                    continue
        return sorted(metas, key=lambda m: m.created, reverse=True)

    def results(self, run_id: str) -> dict[str, Any]:
        out = self.path(run_id)
        meta = self.meta(run_id)

        def load(name: str, default: Any) -> Any:
            p = out / name
            return json.loads(p.read_text("utf-8")) if p.is_file() else default

        if meta.status in ("reading", "assessing"):
            # The app polls this every second while a run is going and shows only the
            # progress: don't read and send the candidate list each time.
            return {
                "meta": meta.model_dump(),
                "results": None,
                "identity": {},
                "candidates": [],
                "screening": None,
                "job": None,
            }
        return {
            "meta": meta.model_dump(),
            "results": load("results.json", None),
            "identity": load("identity_map.json", {}),
            "candidates": load("candidates.json", []),
            "screening": load("screening.json", None),
            "job": self.job(run_id),
        }

    def job(self, run_id: str) -> dict[str, Any] | None:
        """The job profile the run used, as edited, to start another run from it."""
        path = self.path(run_id) / "job.yaml"
        return load_job_profile(path).model_dump(mode="json") if path.is_file() else None

    def original(self, run_id: str, candidate_id: str, file: str) -> Path:
        """A candidate's original document, as listed for this run; KeyError otherwise.

        Only files recorded in the run's candidates.json, inside the assessed folder,
        can be served: a request cannot reach any other file on disk."""
        out = self.path(run_id)
        listing = out / "candidates.json"
        candidates = json.loads(listing.read_text("utf-8")) if listing.is_file() else []
        listed = {
            f["path"] for c in candidates if c.get("id") == candidate_id for f in c.get("files", [])
        }
        if file not in listed:
            raise KeyError(file)
        folder = Path(self.meta(run_id).folder).resolve()
        path = (folder / file).resolve()
        if folder not in path.parents or not path.is_file():
            raise KeyError(file)
        return path

    def redacted(self, run_id: str) -> dict[str, dict[str, str]]:
        p = self.path(run_id) / "redacted.json"
        data: dict[str, dict[str, str]] = json.loads(p.read_text("utf-8")) if p.is_file() else {}
        return data

    # --- running ---------------------------------------------------------------------

    def start(self, request: RunRequest) -> RunMeta:
        settings = self.settings()  # fail now, not in the background, if config is broken
        run_id = f"{request.job.id}-{timestamp()}"
        out = self.runs_dir / run_id
        n = 1
        while out.exists():
            n += 1
            out = self.runs_dir / f"{run_id}-{n}"
        out.mkdir(parents=True)
        meta = RunMeta(
            id=out.name,
            status="reading",
            created=datetime.now(UTC).isoformat(timespec="seconds"),
            folder=request.folder,
            job_title=request.job.title,
            mode=request.mode,
            shortlist=request.shortlist if request.mode == "full" else None,
        )
        self._save(out, meta)
        self._pool.submit(self._run, out, meta, request, settings)
        return meta

    def _run(self, out: Path, meta: RunMeta, request: RunRequest, settings: Settings) -> None:
        try:
            cache_dir = self.runs_dir / ".cache" if self.cache else None
            llm = self.provider(settings, cache_dir)
            with self.reading:
                ingested = ingest(
                    Path(request.folder),
                    overrides=request.overrides,
                    exclude=request.exclude,
                    ocr=ocr_hook(llm, settings.ocr),
                    cache_dir=cache_dir,
                )
            summaries: list[CandidateSummary] = [
                summarize(s, settings.location.home_country) for s in ingested.sources
            ]
            (out / "candidates.json").write_text(
                json.dumps([s.model_dump(mode="json") for s in summaries], indent=2),
                encoding="utf-8",
            )
            if not ingested.candidates:
                raise ValueError("No candidate documents found in this folder.")
            meta.status, meta.total = "assessing", len(ingested.candidates)
            self._save(out, meta)

            def progress(done: int, total: int) -> None:
                meta.done, meta.total = done, total
                self._save(out, meta)

            embed = make_embedder(settings, cache_dir)
            if request.mode == "screen":
                results = screen(request.job, ingested, settings, embed)
                write_screening(out, request.job, results)
                save_job_profile(request.job, out / "job.yaml")
                (out / "identity_map.json").write_text(
                    json.dumps(ingested.identity, indent=2, ensure_ascii=False), "utf-8"
                )
                meta.done = meta.total
            else:
                outcome = assess_all(
                    request.job,
                    ingested,
                    llm,
                    settings,
                    run_id=meta.id,
                    on_progress=progress,
                    shortlist=request.shortlist,
                    embed=embed,
                )
                write_outputs(out, request.job, ingested, outcome)
            meta.status = "done"
        except Exception as e:  # noqa: BLE001 - shown to the user in the app
            meta.status, meta.error = "failed", f"{type(e).__name__}: {e}"
            (out / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        self._save(out, meta)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
