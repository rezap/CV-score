import pytest
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings, ChatOpenAI, OpenAIEmbeddings

from cvscore.config import AzureConfig, ChatModelConfig, EmbeddingsConfig, ModelRole
from cvscore.llm import ConfigurationError, get_chat_model, get_embeddings
from cvscore.llm.factory import is_v1_endpoint, resource_root, temperature_for


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
    assert isinstance(model, AzureChatOpenAI)
    assert model.deployment_name == "main"
    assert model.azure_endpoint == "https://example.openai.azure.com"
    assert model.azure_ad_token_provider is fake_token
    assert model.openai_api_key is None
    assert model.temperature == 0.0


def test_reasoning_model_settings() -> None:
    model = get_chat_model(ModelRole.JUDGE, make_config(), token_provider=fake_token)
    assert isinstance(model, AzureChatOpenAI)
    assert model.deployment_name == "strong"
    assert model.temperature is None
    assert model.reasoning_effort == "high"


def test_embeddings() -> None:
    emb = get_embeddings(make_config(), token_provider=fake_token)
    assert isinstance(emb, AzureOpenAIEmbeddings)
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


@pytest.mark.parametrize(
    ("endpoint", "v1", "root"),
    [
        ("https://x.openai.azure.com/openai/v1", True, "https://x.openai.azure.com"),
        ("https://x.openai.azure.com/openai/v1/", True, "https://x.openai.azure.com"),
        ("https://x.services.ai.azure.com/openai/v1", True, "https://x.services.ai.azure.com"),
        ("https://x.openai.azure.com", False, "https://x.openai.azure.com"),
        (
            "https://x.openai.azure.com/openai/deployments/gpt/chat/completions",
            False,
            "https://x.openai.azure.com",
        ),
    ],
)
def test_endpoint_styles(endpoint: str, v1: bool, root: str) -> None:
    assert is_v1_endpoint(endpoint) is v1
    assert resource_root(endpoint) == root


def test_v1_endpoint_uses_openai_client_with_entra_token() -> None:
    config = make_config("https://x.openai.azure.com/openai/v1")
    model = get_chat_model(ModelRole.SCORER, config, token_provider=fake_token)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "main"
    assert model.openai_api_base == "https://x.openai.azure.com/openai/v1/"
    assert model.openai_api_key is fake_token
    emb = get_embeddings(config, token_provider=fake_token)
    assert isinstance(emb, OpenAIEmbeddings)
    assert emb.model == "embed" and emb.openai_api_key is fake_token
    assert emb.check_embedding_ctx_length is False


def test_classic_endpoint_ignores_a_pasted_path() -> None:
    config = make_config("https://x.openai.azure.com/openai/deployments/main/chat/completions")
    model = get_chat_model(ModelRole.SCORER, config, token_provider=fake_token)
    assert isinstance(model, AzureChatOpenAI)
    assert model.azure_endpoint == "https://x.openai.azure.com"


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        (ChatModelConfig(deployment="gpt-4.1-mini"), 0.0),
        (ChatModelConfig(deployment="gpt-4o"), 0.0),
        (ChatModelConfig(deployment="gpt-5.6-terra"), None),
        (ChatModelConfig(deployment="gpt-6.1-sol"), None),
        (ChatModelConfig(deployment="o4-mini"), None),
        (ChatModelConfig(deployment="my-model", reasoning_effort="low"), None),
        (ChatModelConfig(deployment="gpt-5-chat", temperature=0.2), 0.2),  # set explicitly
    ],
)
def test_temperature_left_out_for_reasoning_models(
    cfg: ChatModelConfig, expected: float | None
) -> None:
    assert temperature_for(cfg, cfg.deployment) == expected
