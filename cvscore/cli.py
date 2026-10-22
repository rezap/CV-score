"""Command-line interface: ``cvscore --help``."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from dotenv import find_dotenv, load_dotenv

from cvscore.config import ModelRole, Provider, Settings, load_settings
from cvscore.ingest import ExtractionError, extract_file, ingest, read_form_table
from cvscore.ingest.discovery import OCR
from cvscore.ingest.ocr import ocr_hook
from cvscore.llm import chat_model, embeddings_model
from cvscore.llm.structured import LLMProvider, settings_provider
from cvscore.models import DocType, SourceDocument
from cvscore.observability import Tracer

app = typer.Typer(help="Rank candidate CVs against a job description.", no_args_is_help=True)
job_app = typer.Typer(help="Create and inspect job profiles.", no_args_is_help=True)
app.add_typer(job_app, name="job")

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config", "-c", help="Config YAML (default: $CVSCORE_CONFIG or ./cvscore.yaml)."
    ),
]


OcrOption = Annotated[
    bool,
    typer.Option(
        "--ocr/--no-ocr",
        help="Read scanned pages and image files with the vision model (see ocr in the config).",
    ),
]


def _ocr_hook(config: Path | None, cache_dir: Path | None, enabled: bool) -> OCR | None:
    """The OCR step, or None (with a note) when it is off or the config can't be loaded."""
    if not enabled:
        return None
    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as e:
        typer.secho(f"OCR is off (no usable config: {e}).", fg="yellow", err=True)
        return None
    return ocr_hook(llm_provider(settings, cache_dir), settings.ocr)


def _load(config: Path | None) -> Settings:
    try:
        return load_settings(config)
    except FileNotFoundError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from e


@app.callback()
def main() -> None:
    """cvscore: explainable CV ranking."""
    # Look for .env from the working directory, not from where the package is installed.
    load_dotenv(find_dotenv(usecwd=True))


@app.command()
def doctor(
    config: ConfigOption = None,
    live: Annotated[
        bool, typer.Option(help="Call each configured model and Langfuse to verify access.")
    ] = False,
) -> None:
    """Check configuration, access to the configured models (Azure or OpenAI), and Langfuse."""
    settings = _load(config)
    ok = True

    typer.echo(f"Provider: {settings.provider}")
    if settings.provider is Provider.OPENAI:
        oa = settings.openai
        key_set = bool(os.environ.get(oa.api_key_env, "").strip())
        typer.echo(
            f"OpenAI base_url: {oa.base_url or '(default)'}  "
            f"{oa.api_key_env}: {'set' if key_set else 'NOT SET'}"
        )
        if not key_set:
            ok = False
        for role in ModelRole:
            m = oa.models.get(role)
            typer.echo(f"  {role:<10} -> {m.model if m else '(not set)'}")
        typer.echo(f"  embeddings -> {oa.embeddings.model if oa.embeddings else '(not set)'}")
        has_embeddings = oa.embeddings is not None
    else:
        az = settings.azure
        from cvscore.llm.factory import is_v1_endpoint

        api = "v1 API" if is_v1_endpoint(az.endpoint) else f"api_version: {az.api_version}"
        typer.echo(f"Azure endpoint: {az.endpoint or '(not set)'}  {api}")
        for role in ModelRole:
            d = az.models.get(role)
            typer.echo(f"  {role:<10} -> {d.deployment if d else '(not set)'}")
        typer.echo(f"  embeddings -> {az.embeddings.deployment if az.embeddings else '(not set)'}")
        has_embeddings = az.embeddings is not None

    if live:
        tracer = Tracer.from_config(settings.langfuse)
        run_config = tracer.run_config(run_id="doctor", trace_name="cvscore-doctor")
        for role in ModelRole:
            try:
                chat_model(role, settings).invoke("Reply with the word OK.", config=run_config)
                typer.secho(f"  {role}: OK", fg="green")
            except Exception as e:  # noqa: BLE001 - report any failure
                ok = False
                typer.secho(f"  {role}: FAILED ({e})", fg="red")
        if has_embeddings:
            try:
                embeddings_model(settings).embed_query("ping")
                typer.secho("  embeddings: OK", fg="green")
            except Exception as e:  # noqa: BLE001
                ok = False
                typer.secho(f"  embeddings: FAILED ({e})", fg="red")
        if settings.langfuse.enabled:
            if tracer.check_auth():
                typer.secho("Langfuse: OK", fg="green")
            else:
                ok = False
                typer.secho("Langfuse: auth check failed", fg="red")
            tracer.flush()
    else:
        typer.echo(f"Langfuse: {'enabled' if settings.langfuse.enabled else 'disabled'}")
        typer.echo("Run with --live to call the services.")

    if not ok:
        raise typer.Exit(1)


@app.command("ingest")
def ingest_cmd(
    candidates: Annotated[
        Path,
        typer.Argument(
            help="Folder with one sub-folder per candidate (or files named <who>_cv.pdf etc.).",
            exists=True,
            file_okay=False,
        ),
    ],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output folder.")] = Path("work"),
    manifest: Annotated[
        Path | None,
        typer.Option(help="CSV with candidate, cv, cover_letter, form columns.", exists=True),
    ] = None,
    forms: Annotated[
        Path | None,
        typer.Option(help="Multi-candidate form export (CSV, one row each).", exists=True),
    ] = None,
    form_key: Annotated[
        str | None,
        typer.Option(help="Column in --forms matching the candidate folder name."),
    ] = None,
    ocr: OcrOption = True,
    config: ConfigOption = None,
) -> None:
    """Extract text from every candidate's documents into JSON files."""
    if forms is not None and form_key is None:
        typer.secho("--form-key is required with --forms.", fg="red", err=True)
        raise typer.Exit(2)
    try:
        form_table = read_form_table(forms, form_key) if forms and form_key else None
        result = ingest(
            candidates,
            manifest=manifest,
            form_table=form_table,
            form_table_path=forms,
            ocr=_ocr_hook(config, out / ".cache", ocr),
            cache_dir=out / ".cache",
        )
    except (ExtractionError, ValueError) as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from e

    docs_dir = out / "candidates"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for cand in result.candidates:
        (docs_dir / f"{cand.candidate_id}.json").write_text(
            cand.model_dump_json(indent=2), encoding="utf-8"
        )
    # Maps pseudonymous IDs back to folder names, which usually identify people.
    (out / "identity_map.json").write_text(
        json.dumps(result.identity, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    problems = 0
    for cand in result.candidates:
        kinds = ", ".join(f"{d.doc_type}({d.quality:.1f})" for d in cand.documents) or "-"
        typer.echo(f"{cand.candidate_id}  {kinds}")
        for d in cand.documents:
            for w in d.warnings:
                problems += 1
                typer.secho(f"    ! {d.path}: {w}", fg="yellow")
        for err in cand.errors:
            problems += 1
            typer.secho(f"    x {err}", fg="red")
    typer.echo(
        f"\n{len(result.candidates)} candidates -> {docs_dir} "
        f"({problems} warnings/errors). identity_map.json contains real names: keep it private."
    )


@app.command()
def inspect(
    files: Annotated[list[Path], typer.Argument(help="Documents to inspect.", exists=True)],
    text: Annotated[bool, typer.Option(help="Also print the extracted text, block by block.")] = (
        True
    ),
    redacted: Annotated[
        bool, typer.Option(help="Show the text as the models would see it (redacted).")
    ] = False,
    ocr: OcrOption = True,
    config: ConfigOption = None,
) -> None:
    """Show what is read from documents: type, name, quality, warnings and the text.

    Use it to see why a file is read badly. Block markers: H heading, P paragraph, L list
    item, T table row; * marks contact details (removed before any model call).
    """
    from cvscore.ingest.discovery import read_file
    from cvscore.redact import redact_blocks

    def ex(path: Path, doc_type: DocType, display: str) -> SourceDocument:
        return extract_file(path, doc_type=doc_type, display_path=display)

    hook = _ocr_hook(config, None, ocr)
    for path in files:
        f = read_file(path, path.parent, ex, ocr=hook)
        typer.secho(f"\n== {path.name}", bold=True)
        if f.document is None:
            typer.secho(f"   cannot read: {f.error}", fg="red")
            continue
        doc = f.document
        names = ", ".join(f"{n} ({where})" for n, where in f.names) or "-"
        typer.echo(f"   type: {f.doc_type} ({f.type_reason})")
        typer.echo(f"   name: {names}")
        typer.echo(f"   quality: {doc.quality:.2f}, {len(doc.text)} characters, {doc.extractor}")
        for w in [*f.warnings, *doc.warnings]:
            typer.secho(f"   ! {w}", fg="yellow")
        if text:
            blocks = redact_blocks(doc.blocks, [n for n, _ in f.names]) if redacted else doc.blocks
            for b in blocks:
                mark = b.kind.value[0].upper() + ("*" if b.is_contact else " ")
                page = f"p{b.page} " if b.page else ""
                typer.echo(f"   {mark} {page}{b.text}")


def llm_provider(settings: Settings, cache_dir: Path | None) -> LLMProvider:
    """The models used by `job draft` and `run` (tests replace this with fakes)."""
    return settings_provider(settings, cache_dir=cache_dir)


@job_app.command("draft")
def job_draft(
    ad: Annotated[Path, typer.Argument(help="Job ad (PDF, DOCX, TXT or MD).", exists=True)],
    out: Annotated[Path, typer.Option("--out", "-o", help="YAML file to write.")] = Path(
        "job.yaml"
    ),
    config: ConfigOption = None,
) -> None:
    """Draft a job profile from a job ad. Review and edit it before `cvscore run`."""
    from cvscore.job import draft_job_profile, save_job_profile

    settings = _load(config)
    try:
        doc = extract_file(ad, doc_type=DocType.OTHER, display_path=ad.name)
    except ExtractionError as e:
        typer.secho(f"Cannot read {ad}: {e}", fg="red", err=True)
        raise typer.Exit(2) from e
    tracer = Tracer.from_config(settings.langfuse)
    run_config = tracer.run_config(run_id=f"job-draft-{_timestamp()}", trace_name="job-draft")
    profile = draft_job_profile(doc.text, llm_provider(settings, None), run_config)
    tracer.flush()
    save_job_profile(profile, out)
    typer.echo(
        f"Wrote {out}: {len(profile.must_have)} must-haves, {len(profile.nice_to_have)} "
        f"nice-to-haves, {len(profile.key_skills)} key skills, {len(profile.traits)} traits.\n"
        "Review and edit it, then run: cvscore run --job "
        f"{out} --candidates <folder>"
    )


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


@app.command("run")
def run_cmd(
    job: Annotated[
        Path, typer.Option(help="Job profile YAML (from `cvscore job draft`).", exists=True)
    ],
    candidates: Annotated[
        Path, typer.Option(help="Candidates folder.", exists=True, file_okay=False)
    ],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output folder.")] = Path("out"),
    manifest: Annotated[Path | None, typer.Option(exists=True)] = None,
    forms: Annotated[Path | None, typer.Option(exists=True)] = None,
    form_key: Annotated[str | None, typer.Option()] = None,
    reveal_names: Annotated[
        bool,
        typer.Option(help="Show folder names in the report instead of pseudonymous IDs."),
    ] = False,
    cache: Annotated[
        bool, typer.Option(help="Reuse model results from earlier runs (out/.cache).")
    ] = True,
    shortlist: Annotated[
        int | None,
        typer.Option(
            help="Screen everyone first (no chat model), then assess only the best N in depth."
        ),
    ] = None,
    ocr: OcrOption = True,
    config: ConfigOption = None,
) -> None:
    """Assess and rank every candidate against the job profile; write the report."""
    from cvscore.job import load_job_profile

    settings = _load(config)
    if forms is not None and form_key is None:
        typer.secho("--form-key is required with --forms.", fg="red", err=True)
        raise typer.Exit(2)
    llm = llm_provider(settings, out / ".cache" if cache else None)
    try:
        profile = load_job_profile(job)
        form_table = read_form_table(forms, form_key) if forms and form_key else None
        ingested = ingest(
            candidates,
            manifest=manifest,
            form_table=form_table,
            form_table_path=forms,
            ocr=ocr_hook(llm, settings.ocr) if ocr else None,
            cache_dir=out / ".cache" if cache else None,
        )
    except (ExtractionError, ValueError) as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from e
    if not ingested.candidates:
        typer.secho(f"No candidates found in {candidates}.", fg="red", err=True)
        raise typer.Exit(2)

    from cvscore.service import assess_all, make_embedder, write_outputs

    n = len(ingested.candidates)
    target = f"the best {min(shortlist, n)} of {n}" if shortlist is not None else str(n)
    typer.echo(f"Assessing {target} candidates for '{profile.title}'...")
    embed = make_embedder(settings, out / ".cache" if cache else None)
    outcome = assess_all(profile, ingested, llm, settings, shortlist=shortlist, embed=embed)
    write_outputs(out, profile, ingested, outcome, reveal_names=reveal_names)
    ranked, run_id = outcome.ranked, outcome.run_id
    names = ingested.identity if reveal_names else None

    for card in ranked:
        label = names.get(card.candidate_id, card.candidate_id) if names else card.candidate_id
        total = "n/a" if card.total is None else f"{round(card.total * 100)}"
        flags = f"  [{', '.join(card.flags)}]" if card.flags else ""
        typer.echo(f"{card.rank:>3}. {label:<30} {total:>4}{flags}")
    typer.echo(
        f"\nReport: {out / 'report.html'} (also ranking.csv, results.json). "
        f"Run id: {run_id}. Treat {out}/ as personal data."
    )


@app.command()
def serve(
    root: Annotated[
        Path | None,
        typer.Option(
            help="Folders under this one can be opened in the app (default: your home folder).",
            file_okay=False,
        ),
    ] = None,
    runs_dir: Annotated[
        Path, typer.Option(help="Where runs (reports, cached model results) are stored.")
    ] = Path("cvscore-runs"),
    host: Annotated[str, typer.Option(help="Use 0.0.0.0 only on a trusted network.")] = (
        "127.0.0.1"
    ),
    port: int = 8000,
    config: ConfigOption = None,
) -> None:
    """Start the web app: pick a folder of applications, upload a job ad, get a ranking."""
    import uvicorn

    from cvscore.web.app import STATIC_DIR, create_app

    root = root or Path.home()
    if not root.is_dir():
        typer.secho(f"Not a folder: {root}", fg="red", err=True)
        raise typer.Exit(2)

    def provider(settings: Settings, cache_dir: Path | None) -> LLMProvider:
        return llm_provider(settings, cache_dir)

    web = create_app(browse_root=root, runs_dir=runs_dir, config=config, provider=provider)
    if not (STATIC_DIR / "index.html").is_file():
        typer.secho(
            "The front end is not built: run `npm install && npm run build` in frontend/.",
            fg="yellow",
        )
    typer.echo(f"cvscore: http://{host}:{port}  (folders under {root.resolve()})")
    uvicorn.run(web, host=host, port=port, log_level="warning")


@app.command("screen")
def screen_cmd(
    job: Annotated[
        Path, typer.Option(help="Job profile YAML (from `cvscore job draft`).", exists=True)
    ],
    candidates: Annotated[
        Path, typer.Option(help="Candidates folder.", exists=True, file_okay=False)
    ],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output folder.")] = Path("screen"),
    manifest: Annotated[Path | None, typer.Option(exists=True)] = None,
    semantic: Annotated[
        bool,
        typer.Option(help="Also match by meaning with the embeddings model, if configured."),
    ] = True,
    reveal_names: Annotated[
        bool, typer.Option(help="Show names in the output instead of pseudonymous IDs.")
    ] = False,
    ocr: OcrOption = True,
    config: ConfigOption = None,
) -> None:
    """Fast, cheap screening: search the CVs for the job's screening criteria (keywords,
    and meaning with embeddings) without a chat model reading them. Scanned CVs still need
    OCR. Edit the criteria under `screening:` in the job YAML."""
    from cvscore.job import load_job_profile
    from cvscore.report import write_screening
    from cvscore.service import make_embedder, screen

    settings = _load(config)
    try:
        profile = load_job_profile(job)
        ingested = ingest(
            candidates,
            manifest=manifest,
            ocr=_ocr_hook(config, out / ".cache", ocr),
            cache_dir=out / ".cache",
        )
    except (ExtractionError, ValueError) as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2) from e
    if not ingested.candidates:
        typer.secho(f"No candidates found in {candidates}.", fg="red", err=True)
        raise typer.Exit(2)
    embed = make_embedder(settings, out / ".cache") if semantic else None
    results = screen(profile, ingested, settings, embed)
    names = ingested.identity if reveal_names else None
    write_screening(out, profile, results, names)
    (out / "identity_map.json").write_text(
        json.dumps(ingested.identity, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    mode = "keywords and meaning" if embed else "keywords"
    typer.echo(f"Screened {len(results)} candidates for '{profile.title}' ({mode}):")
    for r in results:
        label = names.get(r.candidate_id, r.candidate_id) if names else r.candidate_id
        score = "n/a" if r.score is None else f"{round(r.score * 100)}"
        out_note = "  [screened out]" if r.screened_out else ""
        matched = ", ".join(t for c in r.criteria for t in c.matched[:2])
        typer.echo(f"{r.rank:>3}. {label:<30} {score:>4}{out_note}  {matched[:70]}")
    typer.echo(
        f"\nReport: {out / 'screening.html'} (also screening.csv, screening.json). "
        f"Treat {out}/ as personal data."
    )


@app.command("eval")
def eval_cmd(
    set_dir: Annotated[
        Path,
        typer.Argument(
            help="Evaluation set: job.yaml, candidates/, expected.yaml (see evals/README.md).",
            exists=True,
            file_okay=False,
        ),
    ],
    repeats: Annotated[
        int, typer.Option(help="Runs to compare; each makes fresh model calls.", min=1)
    ] = 3,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Write eval.json here.")] = None,
    config: ConfigOption = None,
) -> None:
    """Check the ranking against known answers on fictional candidates: tier order,
    controlled pairs, and how much totals move between runs. Costs real model calls."""
    from cvscore.evaluate import run_eval

    settings = _load(config)
    report = run_eval(set_dir, settings, lambda s: llm_provider(s, None), repeats=repeats)
    typer.echo(report.summary())
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "eval.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"\nWrote {out / 'eval.json'}.")


if __name__ == "__main__":
    app()
