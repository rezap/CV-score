"""Builds LangChain chat and embedding models for the configured provider.

- ``azure``: an Azure AI Foundry / Azure OpenAI resource, authenticated with Microsoft Entra
  ID (a ``DefaultAzureCredential``: environment, managed identity, Azure CLI login, ...). No
  keys. Two endpoint styles are supported:
  - ``https://<resource>.openai.azure.com/openai/v1``: the v1 API. Requests go to
    ``/openai/v1/chat/completions`` with the deployment name as ``model``; no api-version.
  - ``https://<resource>.openai.azure.com``: the classic API. Requests go to
    ``/openai/deployments/<deployment>/chat/completions?api-version=...``. Any path after
    the host (e.g. a pasted ``/openai/deployments/...`` URL) is ignored.
- ``openai``: the OpenAI API (or a compatible endpoint), with an API key read from the
  environment (``OPENAI_API_KEY`` by default).

Nodes never construct clients directly; they ask for a model by role (``chat_model``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from functools import lru_cache
from urllib.parse import urlsplit

from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings, ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from cvscore.config import (
    AzureConfig,
    ChatModelConfig,
    ModelRole,
    OpenAIConfig,
    OpenAIModelConfig,
    Provider,
    Settings,
)

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


def is_v1_endpoint(endpoint: str) -> bool:
    """True for the Azure OpenAI v1 API (``.../openai/v1``)."""
    return urlsplit(endpoint).path.rstrip("/").endswith("/openai/v1")


def resource_root(endpoint: str) -> str:
    """``https://x.openai.azure.com/openai/deployments/...`` -> ``https://x.openai.azure.com``."""
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else endpoint


# Reasoning models (o-series, gpt-5 and later) reject `temperature`.
_REASONING_MODEL_RE = re.compile(r"(?:^|[^a-z0-9])(?:o[1-9]|gpt-?(?:[5-9]|\d{2}))", re.I)


def temperature_for(cfg: ChatModelConfig | OpenAIModelConfig, name: str) -> float | None:
    """The configured temperature; left out for reasoning models unless set explicitly."""
    explicit = "temperature" in cfg.model_fields_set
    if not explicit and (cfg.reasoning_effort is not None or _REASONING_MODEL_RE.search(name)):
        return None
    return cfg.temperature


def get_chat_model(
    role: ModelRole,
    config: AzureConfig,
    *,
    token_provider: TokenProvider | None = None,
) -> BaseChatModel:
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
    tokens = token_provider or entra_token_provider(config.token_scope)
    temperature = temperature_for(model_cfg, model_cfg.deployment)
    if is_v1_endpoint(config.endpoint):
        return ChatOpenAI(
            model=model_cfg.deployment,
            base_url=config.endpoint.rstrip("/") + "/",
            api_key=tokens,  # called before each request: a fresh Entra ID token
            temperature=temperature,
            timeout=config.timeout_s,
            max_retries=config.max_retries,
            **kwargs,
        )
    return AzureChatOpenAI(
        azure_endpoint=resource_root(config.endpoint),
        azure_deployment=model_cfg.deployment,
        api_version=config.api_version,
        azure_ad_token_provider=tokens,
        temperature=temperature,
        timeout=config.timeout_s,
        max_retries=config.max_retries,
        **kwargs,
    )


def get_embeddings(
    config: AzureConfig,
    *,
    token_provider: TokenProvider | None = None,
) -> Embeddings:
    """Embedding model from ``azure.embeddings``."""
    _require_endpoint(config)
    if config.embeddings is None:
        raise ConfigurationError("No embeddings deployment configured (azure.embeddings).")
    tokens = token_provider or entra_token_provider(config.token_scope)
    if is_v1_endpoint(config.endpoint):
        return OpenAIEmbeddings(
            model=config.embeddings.deployment,
            openai_api_base=config.endpoint.rstrip("/") + "/",
            openai_api_key=tokens,
            dimensions=config.embeddings.dimensions,
            # The deployment name is not an OpenAI model name, so don't tokenise locally.
            check_embedding_ctx_length=False,
            request_timeout=config.timeout_s,
            max_retries=config.max_retries,
        )
    return AzureOpenAIEmbeddings(
        azure_endpoint=resource_root(config.endpoint),
        deployment=config.embeddings.deployment,
        openai_api_version=config.api_version,
        azure_ad_token_provider=tokens,
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
        temperature=temperature_for(model_cfg, model_cfg.model),
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
