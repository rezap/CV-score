from pathlib import Path

import pytest

from cvscore.config import ModelRole, load_settings

YAML = """
azure:
  endpoint: https://example.openai.azure.com/
  models:
    scorer:
      deployment: main
    judge:
      deployment: strong
      temperature: null
      reasoning_effort: medium
  embeddings:
    deployment: embed
langfuse:
  enabled: false
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "cvscore.yaml"
    path.write_text(YAML)
    return path


def test_loads_yaml_and_falls_back_to_scorer(config_file: Path) -> None:
    s = load_settings(config_file)
    assert s.azure.endpoint == "https://example.openai.azure.com"
    assert s.azure.models[ModelRole.JUDGE].deployment == "strong"
    assert s.azure.models[ModelRole.JUDGE].temperature is None
    assert s.azure.models[ModelRole.EXTRACTOR].deployment == "main"
    assert s.azure.models[ModelRole.AGENT].deployment == "main"
    assert s.azure.embeddings is not None and s.azure.embeddings.deployment == "embed"
    assert s.langfuse.enabled is False


def test_env_overrides_yaml(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVSCORE_AZURE__ENDPOINT", "https://other.openai.azure.com")
    s = load_settings(config_file)
    assert s.azure.endpoint == "https://other.openai.azure.com"
    assert s.azure.models[ModelRole.SCORER].deployment == "main"


def test_config_env_var_selects_file(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVSCORE_CONFIG", str(config_file))
    assert load_settings().azure.models[ModelRole.SCORER].deployment == "main"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.yaml")


def test_defaults_without_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    s = load_settings()
    assert s.azure.models == {}
    assert s.langfuse.enabled is True
