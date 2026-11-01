"""Configuration of the agent service. Two sources, which never hold the same key (design/4):

  - YAML file         services/agent/config.yaml (committed; another file with AGENT_CONFIG_FILE): every setting but secrets
  - env vars, .env    AGENT_TICKETS__API_KEY=... (.env: services/agent/.env, never committed; env vars win): secrets only

A secret in the YAML, another setting in env vars or .env, or a setting missing from the YAML is an error.
Only the composition root (container.py) reads Settings; adapters receive plain values.
"""

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource

# services/agent/ when running from the repo (this file is services/agent/src/siteops_agent/config.py).
_SERVICE_DIR = Path(__file__).resolve().parents[2]

SECRETS = {"tickets.api_key"}  # the only settings read from env vars and .env


def _config_file() -> Path:
    return Path(os.environ.get("AGENT_CONFIG_FILE", _SERVICE_DIR / "config.yaml"))


def _keys(data: dict[str, Any], prefix: str = "") -> set[str]:
    """Dotted names of the values a source gives: {"rag": {"timeout": ...}} → {"rag.timeout"}."""
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


class RagClientSettings(_Section):
    base_url: str
    timeout: float = Field(gt=0)
    retries: int = Field(ge=0)


class TicketSinkSettings(_Section):
    url: str
    api_key: SecretStr = Field(min_length=16)  # the tickets service's TICKETS_API_KEY (agent/5)
    timeout: float = Field(gt=0)
    retries: int = Field(ge=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_nested_delimiter="__",
        env_file=_SERVICE_DIR / ".env",  # ignored if the file does not exist
        extra="ignore",  # a shared .env may hold other keys; sections still forbid typos
    )

    rag: RagClientSettings
    tickets: TicketSinkSettings

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Earlier sources win: constructor args > env vars > .env > YAML.
        yaml = _YamlWithoutSecrets(settings_cls, yaml_file=_config_file())
        return (init_settings, _SecretsOnly(env_settings), _SecretsOnly(dotenv_settings), yaml)
