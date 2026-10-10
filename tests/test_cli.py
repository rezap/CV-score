from pathlib import Path

from typer.testing import CliRunner

from cvscore.cli import app


def test_doctor_offline(tmp_path: Path) -> None:
    config = tmp_path / "cvscore.yaml"
    config.write_text(
        "azure:\n  endpoint: https://example.openai.azure.com\n"
        "  models:\n    scorer:\n      deployment: main\n"
        "langfuse:\n  enabled: false\n"
    )
    result = CliRunner().invoke(app, ["doctor", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "judge      -> main" in result.output
    assert "embeddings -> (not set)" in result.output


def test_doctor_missing_config(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["doctor", "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 2
