"""Configuration of the rag service.

Sources, highest priority first:
  1. environment variables   RAG_EMBEDDING__MODEL=...   (compose, CI, one-off overrides)
  2. .env file               services/rag/.env           (secrets on a dev machine; never committed)
  3. YAML file               services/rag/config.yaml    (committed; path overridable with RAG_CONFIG_FILE)
  4. the defaults below

Only the composition root (container.py) reads Settings; adapters receive plain values.
"""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# services/rag/ when running from the repo (this file is services/rag/src/siteops_rag/config.py).
_SERVICE_DIR = Path(__file__).resolve().parents[2]


def _config_file() -> Path:
    return Path(os.environ.get("RAG_CONFIG_FILE", _SERVICE_DIR / "config.yaml"))


class _Section(BaseModel):
    """Base for settings sections: immutable, and unknown keys are errors (catches typos in config.yaml)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class EmbeddingSettings(_Section):
    base_url: str = "http://localhost:8001/v1"
    model: str = "BAAI/bge-m3"
    dimension: int = Field(default=1024, gt=0)
    api_key: SecretStr = SecretStr("EMPTY")  # vLLM / Ollama ignore it; the OpenAI SDK requires a value
    timeout: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    batch_size: int = Field(default=32, gt=0)


class LLMSettings(_Section):
    base_url: str = "http://localhost:8000/v1"
    model: str = ""  # required in practice: set it in config.yaml
    api_key: SecretStr = SecretStr("EMPTY")
    timeout: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    temperature: float = Field(default=0.0, ge=0)
    # thinking models: "none" turns thinking off (Ollama); None = don't send it, the server's default applies
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None


class ChunkingSettings(_Section):
    max_length: int = Field(default=1000, gt=0)


class RetrievalSettings(_Section):
    top_k: int = Field(default=5, ge=1, le=50)  # chunks retrieved when a request sends no top_k (rag/7)


class VectorStoreSettings(_Section):
    backend: Literal["memory", "qdrant"] = "qdrant"  # in_memory or qdrant 
    url: str = "http://localhost:6333"
    collection: str = "runbooks"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_nested_delimiter="__",
        env_file=_SERVICE_DIR / ".env",  # ignored if the file does not exist
        extra="ignore",  # a shared .env may hold other keys (HF_TOKEN...); sections still forbid typos
    )

    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    vector_store: VectorStoreSettings = Field(default_factory=VectorStoreSettings)

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
        yaml = YamlConfigSettingsSource(settings_cls, yaml_file=_config_file())
        return (init_settings, env_settings, dotenv_settings, yaml, file_secret_settings)