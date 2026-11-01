"""Configuration of the rag service.

Two sources, which never hold the same key (design/4):
  - YAML file         services/rag/config.yaml (committed; another file with RAG_CONFIG_FILE): every setting but secrets
  - env vars, .env    RAG_LLM__API_KEY=... (.env: services/rag/.env, never committed; env vars win): secrets only

A secret in the YAML, or another setting in env vars or .env, is an error. So is a setting missing from the YAML:
the only code defaults are the API keys (local model servers ignore them) and retrieval.top_k (rag/7).

Only the composition root (container.py) reads Settings; adapters receive plain values.
"""

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# services/rag/ when running from the repo (this file is services/rag/src/siteops_rag/config.py).
_SERVICE_DIR = Path(__file__).resolve().parents[2]


SECRETS = {"embedding.api_key", "llm.api_key"}  # the only settings read from env vars and .env


def _config_file() -> Path:
    return Path(os.environ.get("RAG_CONFIG_FILE", _SERVICE_DIR / "config.yaml"))


def _keys(data: dict[str, Any], prefix: str = "") -> set[str]:
    """Dotted names of the values a source gives: {"llm": {"model": ...}} → {"llm.model"}."""
    keys = set()
    for key, value in data.items():
        keys |= _keys(value, f"{prefix}{key}.") if isinstance(value, dict) else {f"{prefix}{key}"}
    return keys


class _SecretsOnly(PydanticBaseSettingsSource):
    """Env vars or .env, which may only hold secrets."""

    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self._source = source

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:  # abstract; __call__ doesn't use it
        raise NotImplementedError

    def __call__(self) -> dict[str, Any]:
        data = self._source()
        if others := sorted(_keys(data) - SECRETS):
            raise ValueError(f"only secrets come from env vars and .env: set {others} in {_config_file()}")
        return data


class _YamlWithoutSecrets(YamlConfigSettingsSource):
    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        if secrets := sorted(_keys(data) & SECRETS):
            raise ValueError(f"{secrets} are secrets: set them in .env or env vars, not in {_config_file()}")
        return data


class _Section(BaseModel):
    """Base for settings sections: immutable, and unknown keys are errors (catches typos in config.yaml)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class EmbeddingSettings(_Section):
    base_url: str
    model: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    api_key: SecretStr = SecretStr("EMPTY")  # vLLM / Ollama ignore it; the OpenAI SDK requires a value
    timeout: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    batch_size: int = Field(gt=0)


class LLMSettings(_Section):
    base_url: str
    model: str = Field(min_length=1)
    api_key: SecretStr = SecretStr("EMPTY")
    timeout: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    temperature: float = Field(ge=0)
    # thinking models: "none" turns thinking off (Ollama); None (null) = don't send it, the server's default applies
    reasoning_effort: Literal["none", "low", "medium", "high"] | None


class ChunkingSettings(_Section):
    max_length: int = Field(gt=0)


class RetrievalSettings(_Section):
    top_k: int = Field(default=10, ge=1, le=50)  # chunks retrieved when a request sends no top_k (rag/7)


class VectorStoreSettings(_Section):
    backend: Literal["memory", "qdrant"]
    url: str
    collection: str = Field(min_length=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_nested_delimiter="__",
        env_file=_SERVICE_DIR / ".env",  # ignored if the file does not exist
        extra="ignore",  # a shared .env may hold other keys (HF_TOKEN...); sections still forbid typos
    )

    embedding: EmbeddingSettings
    llm: LLMSettings
    chunking: ChunkingSettings
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    vector_store: VectorStoreSettings

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Earlier sources win: constructor args > env vars > .env > YAML > defaults.
        yaml = _YamlWithoutSecrets(settings_cls, yaml_file=_config_file())
        return (init_settings, _SecretsOnly(env_settings), _SecretsOnly(dotenv_settings), yaml)