import pytest

from cvscore.config import AzureConfig, ChatModelConfig, EmbeddingsConfig, ModelRole
from cvscore.llm import ConfigurationError, get_chat_model, get_embeddings


def fake_token() -> str:
    return "fake-entra-token"


def make_config(endpoint: str = "https://example.openai.azure.com") -> AzureConfig:
    return AzureConfig(
        endpoint=endpoint,
        models={
            ModelRole.SCORER: ChatModelConfig(deployment="main"),
            ModelRole.JUDGE: ChatModelConfig(
                deployment="strong", temperature=None, reasoning_effort="high"
            ),
        },
        embeddings=EmbeddingsConfig(deployment="embed", dimensions=1024),
    )


def test_chat_model_uses_role_deployment_and_entra_token() -> None:
    model = get_chat_model(ModelRole.EXTRACTOR, make_config(), token_provider=fake_token)
    assert model.deployment_name == "main"
    assert model.azure_endpoint == "https://example.openai.azure.com"
    assert model.azure_ad_token_provider is fake_token
    assert model.openai_api_key is None
    assert model.temperature == 0.0


def test_reasoning_model_settings() -> None:
    model = get_chat_model(ModelRole.JUDGE, make_config(), token_provider=fake_token)
    assert model.deployment_name == "strong"
    assert model.temperature is None
    assert model.reasoning_effort == "high"


def test_embeddings() -> None:
    emb = get_embeddings(make_config(), token_provider=fake_token)
    assert emb.deployment == "embed"
    assert emb.dimensions == 1024
    assert emb.azure_ad_token_provider is fake_token


def test_missing_endpoint() -> None:
    with pytest.raises(ConfigurationError, match="endpoint"):
        get_chat_model(ModelRole.SCORER, make_config(endpoint=""), token_provider=fake_token)


def test_missing_deployment() -> None:
    config = AzureConfig(endpoint="https://example.openai.azure.com")
    with pytest.raises(ConfigurationError, match="deployment"):
        get_chat_model(ModelRole.SCORER, config, token_provider=fake_token)
    with pytest.raises(ConfigurationError, match="embeddings"):
        get_embeddings(config, token_provider=fake_token)
