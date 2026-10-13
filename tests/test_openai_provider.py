"""OpenAI (API key) provider: configuration, dispatch, and the real request path against a
local fake OpenAI endpoint."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from typer.testing import CliRunner

from cvscore.cli import app
from cvscore.config import ModelRole, Provider, Settings, load_settings
from cvscore.job import JobProfileDraft
from cvscore.llm import ConfigurationError, chat_model, get_openai_chat_model, model_id
from cvscore.llm.structured import settings_provider
from tests.test_azure_structured import DRAFT, FakeAzure, endpoint  # noqa: F401 (fixture)

OPENAI_YAML = """
provider: openai
openai:
  models:
    scorer:
      model: gpt-test-mini
    judge:
      model: gpt-test-large
      temperature: null
      reasoning_effort: high
langfuse:
  enabled: false
"""


@pytest.fixture
def openai_settings(tmp_path: Path) -> Settings:
    path = tmp_path / "cvscore.yaml"
    path.write_text(OPENAI_YAML)
    return load_settings(path)


def test_config_and_role_fallback(openai_settings: Settings) -> None:
    s = openai_settings
    assert s.provider is Provider.OPENAI
    assert s.openai.models[ModelRole.EXTRACTOR].model == "gpt-test-mini"
    assert s.openai.models[ModelRole.JUDGE].reasoning_effort == "high"
    assert s.max_concurrency == s.openai.max_concurrency
    assert model_id(ModelRole.AGENT, s) == "openai:gpt-test-mini"
    assert model_id(ModelRole.JUDGE, s) == "openai:gpt-test-large"


def test_missing_api_key(openai_settings: Settings) -> None:
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY is not set"):
        chat_model(ModelRole.SCORER, openai_settings)


def test_dispatch(openai_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model = chat_model(ModelRole.JUDGE, openai_settings)
    assert isinstance(model, ChatOpenAI) and not isinstance(model, AzureChatOpenAI)
    assert model.model_name == "gpt-test-large"
    assert model.temperature is None and model.reasoning_effort == "high"
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "sk-test"

    # (./cvscore.yaml says openai, so the provider is set explicitly here.)
    azure = Settings(
        provider="azure",
        azure={"endpoint": "https://x.openai.azure.com", "models": {"scorer": {"deployment": "d"}}},
    )
    assert isinstance(chat_model(ModelRole.SCORER, azure), AzureChatOpenAI)


def test_custom_key_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(OPENAI_YAML.replace("openai:\n", "openai:\n  api_key_env: MY_KEY\n", 1))
    monkeypatch.setenv("MY_KEY", "sk-mine")
    model = get_openai_chat_model(ModelRole.SCORER, load_settings(path).openai)
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "sk-mine"


def test_doctor_reports_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "cvscore.yaml").write_text(OPENAI_YAML)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "Provider: openai" in result.output
    assert "OPENAI_API_KEY: NOT SET" in result.output
    assert "judge      -> gpt-test-large" in result.output

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY: set" in result.output
    assert "sk-test" not in result.output  # never print the key


def test_structured_output_request(
    endpoint: str,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    path = tmp_path / "c.yaml"
    path.write_text(
        OPENAI_YAML.replace(
            "openai:\n", f"openai:\n  base_url: {endpoint}/v1\n  max_retries: 0\n", 1
        )
    )
    llm = settings_provider(load_settings(path), cache_dir=tmp_path / "cache")(ModelRole.AGENT)
    assert llm.model_id == "openai:gpt-test-mini"

    result = llm.generate(JobProfileDraft, [SystemMessage("system"), HumanMessage("ad")], None)
    assert result == JobProfileDraft.model_validate(DRAFT)

    [req] = FakeAzure.requests
    assert req["path"] == "/v1/chat/completions"
    assert req["auth"] == "Bearer sk-test"
    body = req["body"]
    assert body["model"] == "gpt-test-mini"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert json.loads(json.dumps(body["messages"]))[1]["content"] == "ad"

    llm.generate(JobProfileDraft, [SystemMessage("system"), HumanMessage("ad")], None)
    assert len(FakeAzure.requests) == 1  # cached
