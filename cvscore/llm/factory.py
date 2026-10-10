"""Builds LangChain chat and embedding models for an Azure AI Foundry project.

Authentication is Microsoft Entra ID: a ``DefaultAzureCredential`` (environment,
managed identity, Azure CLI login, ...) issues bearer tokens for the Cognitive
Services scope. No API keys are used.

Nodes never construct clients directly; they ask for a model by role.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

from cvscore.config import AzureConfig, ModelRole

TokenProvider = Callable[[], str]


class ConfigurationError(ValueError):
    """Raised when the Azure configuration is missing something a model needs."""


@lru_cache(maxsize=1)
def _default_credential() -> TokenCredential:
    return DefaultAzureCredential()


def entra_token_provider(scope: str, credential: TokenCredential | None = None) -> TokenProvider:
    """Return a callable that yields a fresh Entra ID bearer token for ``scope``."""
    return get_bearer_token_provider(credential or _default_credential(), scope)


def _require_endpoint(config: AzureConfig) -> None:
    if not config.endpoint:
        raise ConfigurationError(
            "azure.endpoint is not set (config file or CVSCORE_AZURE__ENDPOINT)."
        )


def get_chat_model(
    role: ModelRole,
    config: AzureConfig,
    *,
    token_provider: TokenProvider | None = None,
) -> AzureChatOpenAI:
    """Chat model for ``role``, using the deployment configured for it."""
    _require_endpoint(config)
    model_cfg = config.models.get(role)
    if model_cfg is None:
        raise ConfigurationError(
            f"No deployment configured for role '{role}'. Add azure.models.{role}.deployment."
        )
    kwargs: dict[str, object] = {}
    if model_cfg.max_tokens is not None:
        kwargs["max_tokens"] = model_cfg.max_tokens
    if model_cfg.reasoning_effort is not None:
        kwargs["reasoning_effort"] = model_cfg.reasoning_effort
    return AzureChatOpenAI(
        azure_endpoint=config.endpoint,
        azure_deployment=model_cfg.deployment,
        api_version=config.api_version,
        azure_ad_token_provider=token_provider or entra_token_provider(config.token_scope),
        temperature=model_cfg.temperature,
        timeout=config.timeout_s,
        max_retries=config.max_retries,
        **kwargs,
    )


def get_embeddings(
    config: AzureConfig,
    *,
    token_provider: TokenProvider | None = None,
) -> AzureOpenAIEmbeddings:
    """Embedding model from ``azure.embeddings``."""
    _require_endpoint(config)
    if config.embeddings is None:
        raise ConfigurationError("No embeddings deployment configured (azure.embeddings).")
    return AzureOpenAIEmbeddings(
        azure_endpoint=config.endpoint,
        deployment=config.embeddings.deployment,
        openai_api_version=config.api_version,
        azure_ad_token_provider=token_provider or entra_token_provider(config.token_scope),
        dimensions=config.embeddings.dimensions,
        request_timeout=config.timeout_s,
        max_retries=config.max_retries,
    )
