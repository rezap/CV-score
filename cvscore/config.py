"""Settings for cvscore, loaded from a YAML file with environment-variable overrides.

Precedence (highest first): explicit init kwargs, environment variables
(``CVSCORE_`` prefix, ``__`` as the nesting delimiter, e.g.
``CVSCORE_AZURE__ENDPOINT``), the YAML file, then defaults.

Secrets never live in the YAML file: Azure uses Entra ID, OpenAI reads its key from
``OPENAI_API_KEY``, and Langfuse reads its keys from the standard ``LANGFUSE_PUBLIC_KEY`` /
``LANGFUSE_SECRET_KEY`` variables.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

DEFAULT_CONFIG_PATH = Path("cvscore.yaml")
CONFIG_PATH_ENV = "CVSCORE_CONFIG"
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


class Provider(StrEnum):
    AZURE = "azure"  # Azure OpenAI in an Azure AI Foundry project, Entra ID auth
    OPENAI = "openai"  # OpenAI API (or a compatible endpoint), API key auth


class ModelRole(StrEnum):
    """What a chat model is used for. Each role maps to a deployment / model."""

    EXTRACTOR = "extractor"  # high-volume structured extraction
    SCORER = "scorer"  # rubric scoring
    AGENT = "agent"  # tool-calling agents (job profiler, evidence agent)
    JUDGE = "judge"  # pairwise comparison, shortlist critic


class ChatModelConfig(BaseModel):
    deployment: str
    temperature: float | None = 0.0
    max_tokens: int | None = None
    # Reasoning models (o-series, gpt-5) reject `temperature`; set it to null for those.
    reasoning_effort: str | None = None


class EmbeddingsConfig(BaseModel):
    deployment: str
    dimensions: int | None = None


class AzureConfig(BaseModel):
    endpoint: str = ""
    api_version: str = "2024-10-21"
    token_scope: str = COGNITIVE_SERVICES_SCOPE
    models: dict[ModelRole, ChatModelConfig] = Field(default_factory=dict)
    embeddings: EmbeddingsConfig | None = None
    timeout_s: float = 120.0
    max_retries: int = 6
    max_concurrency: int = 8

    @field_validator("endpoint")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def _roles_fall_back(self) -> AzureConfig:
        _fill_roles(self.models)
        return self


M = TypeVar("M")


def _fill_roles(models: dict[ModelRole, M]) -> None:
    """Roles without their own model reuse the scorer's (or any configured) model."""
    if not models:
        return
    default = models.get(ModelRole.SCORER) or next(iter(models.values()))
    for role in ModelRole:
        models.setdefault(role, default)


class OpenAIModelConfig(BaseModel):
    model: str
    temperature: float | None = 0.0
    max_tokens: int | None = None
    # Reasoning models reject `temperature`; set it to null for those.
    reasoning_effort: str | None = None


class OpenAIEmbeddingsConfig(BaseModel):
    model: str
    dimensions: int | None = None


class OpenAIConfig(BaseModel):
    """OpenAI API. The key is read from the environment variable named in ``api_key_env``
    (``OPENAI_API_KEY`` by default), never from the config file."""

    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None  # for OpenAI-compatible endpoints or proxies
    organization: str | None = None
    models: dict[ModelRole, OpenAIModelConfig] = Field(default_factory=dict)
    embeddings: OpenAIEmbeddingsConfig | None = None
    timeout_s: float = 120.0
    max_retries: int = 6
    max_concurrency: int = 8

    @model_validator(mode="after")
    def _roles_fall_back(self) -> OpenAIConfig:
        _fill_roles(self.models)
        return self


class LangfuseConfig(BaseModel):
    enabled: bool = True
    # Keys come from LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY; host may also come
    # from LANGFUSE_BASE_URL. A value here overrides the environment.
    host: str | None = None
    environment: str | None = None
    release: str | None = None
    sample_rate: float | None = None
    mask_pii: bool = True


class RankingConfig(BaseModel):
    # "flag": candidates failing a must-have are ranked last but keep their score.
    # "exclude": they get no total score (still listed, for a person to overrule).
    knockout: Literal["flag", "exclude"] = "flag"
    low_confidence: float = 0.5  # dimension confidence below this raises a flag
    max_dropped_quotes: int = 3  # this many unverifiable model quotes raises a flag


class OCRConfig(BaseModel):
    """Reading scanned CVs (PDF pages without text, image files) with a vision model.

    The model sees the page image before redaction, so these calls are never traced.
    """

    enabled: bool = True
    role: ModelRole = ModelRole.EXTRACTOR  # whose deployment reads the pages
    dpi: int = 150
    max_pages: int = 6  # per document


class ScreeningConfig(BaseModel):
    """Fast screening (``cvscore screen``, ``run --shortlist``): keywords, plus semantic
    search when an embeddings model is configured."""

    semantic: bool = True
    # Cosine similarity of a criterion to the closest CV passage that counts as no match
    # (floor) and as a full match (full); tuned for OpenAI text-embedding-3 models.
    similarity_floor: float = 0.25
    similarity_full: float = 0.55
    # A required criterion without keyword matches still passes with this semantic score.
    required_semantic: float = 0.6


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CVSCORE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    provider: Provider = Provider.AZURE
    azure: AzureConfig = Field(default_factory=AzureConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)

    @property
    def max_concurrency(self) -> int:
        return (
            self.openai.max_concurrency
            if self.provider is Provider.OPENAI
            else self.azure.max_concurrency
        )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_file = settings_cls.model_config.get("yaml_file") or os.environ.get(
            CONFIG_PATH_ENV, str(DEFAULT_CONFIG_PATH)
        )
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file),
        )


def load_settings(path: str | Path | None = None, **overrides: Any) -> Settings:
    """Load settings from ``path`` (or ``$CVSCORE_CONFIG``, or ``./cvscore.yaml``)."""
    if path is None:
        return Settings(**overrides)
    if not Path(path).is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    class _FileSettings(Settings):
        model_config = SettingsConfigDict(yaml_file=path)

    return _FileSettings(**overrides)
