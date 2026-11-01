"""Configuration of the tickets service. Two sources, which never hold the same key (design/4):

  - YAML file         services/tickets/config.yaml (committed; another file with TICKETS_CONFIG_FILE,
                      e.g. config.docker.yaml in the image): every setting but the API key
  - env vars, .env    TICKETS_API_KEY only (.env: services/tickets/.env, never committed; env vars win)

  TICKETS_API_KEY  required: clients send it as `Authorization: Bearer <key>` (agent/5)

The API key in the YAML, another setting in env vars or .env, or a setting missing from the YAML is an error.
"""

import os
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource

# services/tickets/ when running from the repo (this file is services/tickets/src/siteops_tickets/config.py).
_SERVICE_DIR = Path(__file__).resolve().parents[2]

SECRETS = {"api_key"}  # the only settings read from env vars and .env


def _config_file() -> Path:
    return Path(os.environ.get("TICKETS_CONFIG_FILE", _SERVICE_DIR / "config.yaml"))


class _SecretsOnly(PydanticBaseSettingsSource):
    """Env vars or .env, which may only hold secrets."""

    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self._source = source

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:  # abstract; __call__ doesn't use it
        raise NotImplementedError

    def __call__(self) -> dict[str, Any]:
        data = self._source()
        if others := sorted(set(data) - SECRETS):
            raise ValueError(f"only secrets come from env vars and .env: set {others} in {_config_file()}")
        return data


class _YamlWithoutSecrets(YamlConfigSettingsSource):
    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        if secrets := sorted(set(data) & SECRETS):
            raise ValueError(f"{secrets} are secrets: set them in .env or env vars, not in {_config_file()}")
        return data


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TICKETS_", env_file=_SERVICE_DIR / ".env", extra="ignore")

    api_key: SecretStr = Field(min_length=16)  # no default: the service refuses to start without a key
    db_path: Path
    db_lock_timeout: float = Field(gt=0)
    dedup_minutes: int = Field(gt=0)
    host: str  # 127.0.0.1 also turns on the SDK's localhost-only Host check, so a container binds 0.0.0.0
    port: int = Field(gt=0, lt=65536)

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
