"""Command-line interface: ``cvscore --help``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from dotenv import find_dotenv, load_dotenv

from cvscore.config import ModelRole, Settings, load_settings
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


if __name__ == "__main__":
    app()
