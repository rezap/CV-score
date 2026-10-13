"""Builds LangChain chat and embedding models for the configured provider.

- ``azure``: an Azure AI Foundry project, authenticated with Microsoft Entra ID (a
  ``DefaultAzureCredential``: environment, managed identity, Azure CLI login, ...). No keys.
- ``openai``: the OpenAI API (or a compatible endpoint), with an API key read from the
  environment (``OPENAI_API_KEY`` by default).

Nodes never construct clients directly; they ask for a model by role (``chat_model``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import lru_cache

from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings, ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from cvscore.config import AzureConfig, ModelRole, OpenAIConfig, Provider, Settings

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


def openai_api_key(config: OpenAIConfig) -> str:
    key = os.environ.get(config.api_key_env, "").strip()
    if not key:
        raise ConfigurationError(f"{config.api_key_env} is not set (the OpenAI API key).")
    return key


def get_openai_chat_model(
    role: ModelRole, config: OpenAIConfig, *, api_key: str | None = None
) -> ChatOpenAI:
    """Chat model for ``role`` from the OpenAI API."""
    model_cfg = config.models.get(role)
    if model_cfg is None:
        raise ConfigurationError(
            f"No model configured for role '{role}'. Add openai.models.{role}.model."
        )
    kwargs: dict[str, object] = {}
    if model_cfg.max_tokens is not None:
        kwargs["max_tokens"] = model_cfg.max_tokens
    if model_cfg.reasoning_effort is not None:
        kwargs["reasoning_effort"] = model_cfg.reasoning_effort
    return ChatOpenAI(
        model=model_cfg.model,
        api_key=SecretStr(api_key or openai_api_key(config)),
        base_url=config.base_url,
        organization=config.organization,
        temperature=model_cfg.temperature,
        timeout=config.timeout_s,
        max_retries=config.max_retries,
        **kwargs,
    )


def get_openai_embeddings(config: OpenAIConfig, *, api_key: str | None = None) -> OpenAIEmbeddings:
    if config.embeddings is None:
        raise ConfigurationError("No embeddings model configured (openai.embeddings).")
    return OpenAIEmbeddings(
        model=config.embeddings.model,
        openai_api_key=SecretStr(api_key or openai_api_key(config)),
        openai_api_base=config.base_url,
        dimensions=config.embeddings.dimensions,
        request_timeout=config.timeout_s,
        max_retries=config.max_retries,
    )


def chat_model(role: ModelRole, settings: Settings) -> BaseChatModel:
    """Chat model for ``role`` from the configured provider."""
    if settings.provider is Provider.OPENAI:
        return get_openai_chat_model(role, settings.openai)
    return get_chat_model(role, settings.azure)


def embeddings_model(settings: Settings) -> Embeddings:
    if settings.provider is Provider.OPENAI:
        return get_openai_embeddings(settings.openai)
    return get_embeddings(settings.azure)


def model_id(role: ModelRole, settings: Settings) -> str:
    """Stable name for caching and reports, e.g. ``azure:gpt-4o`` or ``openai:gpt-4.1``."""
    if settings.provider is Provider.OPENAI:
        cfg = settings.openai.models.get(role)
        return f"openai:{cfg.model if cfg else '?'}"
    deployment = settings.azure.models.get(role)
    return f"azure:{deployment.deployment if deployment else '?'}"
