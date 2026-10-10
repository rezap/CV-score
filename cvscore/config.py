"""Settings for cvscore, loaded from a YAML file with environment-variable overrides.

Precedence (highest first): explicit init kwargs, environment variables
(``CVSCORE_`` prefix, ``__`` as the nesting delimiter, e.g.
``CVSCORE_AZURE__ENDPOINT``), the YAML file, then defaults.

Secrets never live in the YAML file: Azure uses Entra ID, and Langfuse reads its
keys from the standard ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` variables.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Any

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


class ModelRole(StrEnum):
    """What a chat model is used for. Each role maps to an Azure deployment."""

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
        """Roles without their own deployment reuse the scorer's (or any) deployment."""
        if not self.models:
            return self
        default = self.models.get(ModelRole.SCORER) or next(iter(self.models.values()))
        for role in ModelRole:
            self.models.setdefault(role, default)
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CVSCORE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    azure: AzureConfig = Field(default_factory=AzureConfig)
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)

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
