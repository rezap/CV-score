"""Command-line interface: ``cvscore --help``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from dotenv import find_dotenv, load_dotenv

from cvscore.config import ModelRole, Settings, load_settings
from cvscore.ingest import ExtractionError, ingest, read_form_table
from cvscore.llm import get_chat_model, get_embeddings
from cvscore.observability import Tracer

app = typer.Typer(help="Rank candidate CVs against a job description.", no_args_is_help=True)

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config", "-c", help="Config YAML (default: $CVSCORE_CONFIG or ./cvscore.yaml)."
    ),
]


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
        bool, typer.Option(help="Call each Azure deployment and Langfuse to verify access.")
    ] = False,
) -> None:
    """Check configuration, Entra ID access to Azure deployments, and Langfuse."""
    settings = _load(config)
    az = settings.azure
    ok = True

    typer.echo(f"Azure endpoint: {az.endpoint or '(not set)'}  api_version: {az.api_version}")
    for role in ModelRole:
        model_cfg = az.models.get(role)
        typer.echo(f"  {role:<10} -> {model_cfg.deployment if model_cfg else '(not set)'}")
    typer.echo(f"  embeddings -> {az.embeddings.deployment if az.embeddings else '(not set)'}")

    if live:
        tracer = Tracer.from_config(settings.langfuse)
        run_config = tracer.run_config(run_id="doctor", trace_name="cvscore-doctor")
        for role in ModelRole:
            try:
                get_chat_model(role, az).invoke("Reply with the word OK.", config=run_config)
                typer.secho(f"  {role}: OK", fg="green")
            except Exception as e:  # noqa: BLE001 - report any failure
                ok = False
                typer.secho(f"  {role}: FAILED ({e})", fg="red")
        if az.embeddings:
            try:
                get_embeddings(az).embed_query("ping")
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
) -> None:
    """Extract text from every candidate's documents into JSON files."""
    if forms is not None and form_key is None:
        typer.secho("--form-key is required with --forms.", fg="red", err=True)
        raise typer.Exit(2)
    try:
        form_table = read_form_table(forms, form_key) if forms and form_key else None
        result = ingest(candidates, manifest=manifest, form_table=form_table, form_table_path=forms)
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


if __name__ == "__main__":
    app()
