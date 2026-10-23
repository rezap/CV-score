"""FastAPI back end for the web app (``cvscore serve``).

It is meant to run on your own machine (it binds to 127.0.0.1 by default): it reads
folders on disk, below the browse root you start it with.

    GET  /api/status                   provider, models and whether they are configured
    GET  /api/settings                 every setting in the config file, and which are overridden
    PUT  /api/settings                 validate and save settings to the config file
    GET  /api/fs?path=                 sub-folders and documents of a folder
    POST /api/scan                     group a folder's files into candidates (no model calls)
    POST /api/job                      job ad (PDF/DOCX/TXT/MD or pasted text) -> drafted profile;
                                       job.yaml -> loaded profile
    POST /api/runs                     start assessing a folder against a profile
    GET  /api/runs                     earlier runs
    GET  /api/runs/{id}                status, progress, and results when done
    GET  /api/runs/{id}/redacted/{cid} the redacted text the models saw for one candidate
    GET  /api/runs/{id}/files/{name}   report.html, ranking.csv, results.json, job.yaml
    GET  /api/runs/{id}/original/{cid}?file=  a candidate's original document
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from cvscore.config import (
    ModelRole,
    Provider,
    Settings,
    config_path,
    env_overrides,
    load_settings,
    save_settings,
)
from cvscore.ingest import ExtractionError, extract_file
from cvscore.ingest.cache import cached_extractor
from cvscore.ingest.discovery import (
    OCR,
    SUPPORTED_SUFFIXES,
    discover_folder,
    is_candidate_file,
)
from cvscore.ingest.ocr import ocr_hook
from cvscore.ingest.pipeline import extract_document
from cvscore.job import draft_job_profile
from cvscore.llm.structured import LLMProvider, settings_provider
from cvscore.location import DEFAULT_HOME, country_name, in_eu
from cvscore.models import DocType, JobProfile
from cvscore.observability import Tracer
from cvscore.service import timestamp
from cvscore.web.runs import ProviderFactory, RunManager, RunMeta, RunRequest
from cvscore.web.schemas import CandidateSummary, summarize

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
JOB_AD_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".doc", ".rtf", ".odt"}
RUN_FILES = {
    "report.html",
    "ranking.csv",
    "results.json",
    "job.yaml",
    "screening.html",
    "screening.csv",
    "screening.json",
}


class FolderEntry(BaseModel):
    name: str
    path: str
    documents: int  # supported files directly inside


class FolderListing(BaseModel):
    path: str
    parent: str | None
    root: str
    folders: list[FolderEntry]
    documents: list[str]


class ScanRequest(BaseModel):
    folder: str
    overrides: dict[str, DocType] = {}
    exclude: list[str] = []


class ScanResult(BaseModel):
    folder: str
    candidates: list[CandidateSummary]


class JobResult(BaseModel):
    profile: JobProfile
    source: str  # "drafted" or "loaded"


def _default_provider(settings: Settings, cache_dir: Path | None) -> LLMProvider:
    return settings_provider(settings, cache_dir=cache_dir)


def create_app(
    *,
    browse_root: Path,
    runs_dir: Path,
    config: Path | None = None,
    provider: ProviderFactory = _default_provider,
    static_dir: Path | None = STATIC_DIR,
) -> FastAPI:
    browse_root = browse_root.expanduser().resolve()

    def settings() -> Settings:
        """Read the config on every use, so edits apply without a restart."""
        try:
            return load_settings(config)
        except (FileNotFoundError, ValidationError, ValueError) as e:
            raise HTTPException(400, f"Configuration problem: {e}") from e

    runs = RunManager(runs_dir, settings, provider)

    def scan_ocr() -> OCR | None:
        """OCR while scanning, so scanned CVs are grouped by the name in them. Results are
        cached with the runs', so the run that follows doesn't read the pages again."""
        try:
            s = load_settings(config)
        except (FileNotFoundError, ValidationError, ValueError):
            return None
        return ocr_hook(provider(s, runs_dir / ".cache"), s.ocr)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        runs.shutdown()

    app = FastAPI(title="cvscore", lifespan=lifespan)

    def inside_root(raw: str | None) -> Path:
        path = Path(raw).expanduser() if raw else browse_root
        if not path.is_absolute():
            path = browse_root / path
        path = path.resolve()
        if path != browse_root and browse_root not in path.parents:
            raise HTTPException(403, f"Only folders under {browse_root} can be opened.")
        if not path.is_dir():
            raise HTTPException(404, f"Not a folder: {path}")
        return path

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        path = config_path(config)
        try:
            current = load_settings(config) if path.is_file() else Settings.model_validate({})
            problem = None
        except (ValidationError, ValueError) as e:
            current, problem = Settings.model_validate({}), str(e)
        return {
            "path": str(path.resolve()),
            "exists": path.is_file(),
            "problem": problem,
            "settings": current.model_dump(mode="json"),
            # Values from these variables win over the file, so edits to them show no effect.
            "env_overrides": env_overrides(),
            # Secrets are never read or written here; only whether they are set.
            "secrets": {
                name: bool(os.environ.get(name, "").strip())
                for name in [
                    current.openai.api_key_env,
                    "LANGFUSE_PUBLIC_KEY",
                    "LANGFUSE_SECRET_KEY",
                ]
            },
        }

    @app.put("/api/settings")
    def put_settings(body: dict[str, Any]) -> dict[str, Any]:
        try:
            new = Settings.model_validate(body)
        except ValidationError as e:
            raise HTTPException(422, f"Invalid settings: {e}") from e
        backup = save_settings(new, config_path(config))
        result = get_settings()
        result["backup"] = str(backup) if backup else None
        return result

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        try:
            s = load_settings(config)
        except (FileNotFoundError, ValidationError, ValueError) as e:
            return {
                "ready": False,
                "problems": [str(e)],
                "browse_root": str(browse_root),
                "home_country": DEFAULT_HOME,
                "home_name": country_name(DEFAULT_HOME),
                "home_in_eu": in_eu(DEFAULT_HOME),
            }
        problems: list[str] = []
        if s.provider is Provider.OPENAI:
            models = {
                r.value: (m.model if m else None)
                for r in ModelRole
                for m in [s.openai.models.get(r)]
            }
            if not os.environ.get(s.openai.api_key_env, "").strip():
                problems.append(f"{s.openai.api_key_env} is not set.")
        else:
            models = {
                r.value: (d.deployment if d else None)
                for r in ModelRole
                for d in [s.azure.models.get(r)]
            }
            if not s.azure.endpoint:
                problems.append("azure.endpoint is not set in the config.")
        if not models.get(ModelRole.SCORER.value):
            problems.append("No scorer model is configured.")
        return {
            "ready": not problems,
            "problems": problems,
            "provider": s.provider.value,
            "models": models,
            "langfuse": s.langfuse.enabled,
            "browse_root": str(browse_root),
            "home_country": s.location.home_country,
            "home_name": country_name(s.location.home_country),
            "home_in_eu": in_eu(s.location.home_country),
        }

    @app.get("/api/fs")
    def list_folder(path: str | None = None) -> FolderListing:
        folder = inside_root(path)
        folders: list[FolderEntry] = []
        documents: list[str] = []
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except PermissionError as e:
            raise HTTPException(403, f"Cannot read {folder}") from e
        for p in entries:
            if p.name.startswith("."):
                continue
            if p.is_dir():
                try:
                    count = sum(1 for c in p.iterdir() if is_candidate_file(c))
                except OSError:
                    continue
                folders.append(FolderEntry(name=p.name, path=str(p), documents=count))
            elif p.suffix.lower() in SUPPORTED_SUFFIXES and is_candidate_file(p):
                documents.append(p.name)
        return FolderListing(
            path=str(folder),
            parent=str(folder.parent) if folder != browse_root else None,
            root=str(browse_root),
            folders=folders,
            documents=documents,
        )

    @app.post("/api/scan")
    def scan(request: ScanRequest) -> ScanResult:
        folder = inside_root(request.folder)
        with runs.reading:
            sources = discover_folder(
                folder,
                cached_extractor(extract_document, runs_dir / ".cache"),
                overrides=request.overrides,
                exclude=request.exclude,
                ocr=scan_ocr(),
            )
        try:
            home = load_settings(config).location.home_country
        except (FileNotFoundError, ValidationError, ValueError):
            home = DEFAULT_HOME
        return ScanResult(folder=str(folder), candidates=[summarize(s, home) for s in sources])

    @app.post("/api/job")
    def job(
        file: Annotated[UploadFile | None, File()] = None,
        text: Annotated[str | None, Form()] = None,
    ) -> JobResult:
        if file is None and not (text and text.strip()):
            raise HTTPException(422, "Upload a job ad or paste its text.")
        if file is not None:
            data = file.file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "The file is larger than 10 MB.")
            suffix = Path(file.filename or "").suffix.lower()
            if suffix in (".yaml", ".yml", ".json"):
                try:
                    profile = JobProfile.model_validate(yaml.safe_load(data.decode("utf-8")))
                except (ValidationError, yaml.YAMLError, UnicodeDecodeError) as e:
                    raise HTTPException(422, f"Not a valid job profile: {e}") from e
                return JobResult(profile=profile, source="loaded")
            if suffix not in JOB_AD_SUFFIXES:
                raise HTTPException(415, f"Unsupported file type: {suffix or '(none)'}")
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"job_ad{suffix}"
                path.write_bytes(data)
                try:
                    ad_text = extract_file(
                        path, doc_type=DocType.OTHER, display_path=path.name
                    ).text
                except ExtractionError as e:
                    raise HTTPException(422, f"Cannot read the job ad: {e}") from e
        else:
            ad_text = text or ""
        s = settings()
        tracer = Tracer.from_config(s.langfuse)
        run_config = tracer.run_config(run_id=f"job-draft-{timestamp()}", trace_name="job-draft")
        try:
            # Cached with the runs' model calls: the same ad gives the same draft at once.
            profile = draft_job_profile(ad_text, provider(s, runs_dir / ".cache"), run_config)
        except Exception as e:  # noqa: BLE001 - model or network errors go back to the user
            raise HTTPException(502, f"The model call failed: {type(e).__name__}: {e}") from e
        finally:
            tracer.flush()
        return JobResult(profile=profile, source="drafted")

    @app.post("/api/runs")
    def start_run(request: RunRequest) -> RunMeta:
        request.folder = str(inside_root(request.folder))
        return runs.start(request)

    @app.get("/api/runs")
    def list_runs() -> list[RunMeta]:
        return runs.list_runs()

    def _run_or_404(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except (KeyError, FileNotFoundError) as e:
            raise HTTPException(404, "No such run.") from e

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        result: dict[str, Any] = _run_or_404(lambda: runs.results(run_id))
        return result

    @app.get("/api/runs/{run_id}/redacted/{candidate_id}")
    def redacted(run_id: str, candidate_id: str) -> dict[str, str]:
        docs: dict[str, dict[str, str]] = _run_or_404(lambda: runs.redacted(run_id))
        if candidate_id not in docs:
            raise HTTPException(404, "No such candidate in this run.")
        return docs[candidate_id]

    @app.get("/api/runs/{run_id}/files/{name}")
    def run_file(run_id: str, name: str) -> FileResponse:
        if name not in RUN_FILES:
            raise HTTPException(404, "No such file.")
        path: Path = _run_or_404(lambda: runs.path(run_id)) / name
        if not path.is_file():
            raise HTTPException(404, "Not written yet.")
        return FileResponse(path, filename=f"{run_id}-{name}" if name != "report.html" else None)

    @app.get("/api/runs/{run_id}/original/{candidate_id}")
    def original(run_id: str, candidate_id: str, file: str) -> FileResponse:
        """A candidate's original document (CV, letter, form), opened in the browser."""
        path: Path = _run_or_404(lambda: runs.original(run_id, candidate_id, file))
        return FileResponse(path, filename=path.name, content_disposition_type="inline")

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    else:

        @app.get("/")
        def no_frontend() -> dict[str, str]:
            return {
                "message": "The web front end is not built. Run `npm install && npm run build` "
                "in frontend/, or use the API under /api."
            }

    return app
