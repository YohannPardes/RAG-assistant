"""Configuration of the tickets service, from environment variables only (design/4):

  TICKETS_API_KEY        required: clients send it as `Authorization: Bearer <key>` (agent/5)
  TICKETS_DB_PATH        the SQLite file (default: tickets.db in the working directory)
  TICKETS_DEDUP_MINUTES  agent/3's dedup window (default: 60)
  TICKETS_HOST           bind address; 127.0.0.1 also turns on the SDK's localhost-only Host check,
                         so a container sets 0.0.0.0 and relies on the API key alone
  TICKETS_PORT           (default: 8000)

A services/tickets/.env file is read too (a dev machine's key; never committed).
"""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# services/tickets/ when running from the repo (this file is services/tickets/src/siteops_tickets/config.py).
_SERVICE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TICKETS_", env_file=_SERVICE_DIR / ".env", extra="ignore")

    api_key: SecretStr = Field(min_length=16)  # no default: the service refuses to start without a key
    db_path: Path = Path("tickets.db")
    dedup_minutes: int = Field(default=60, gt=0)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, gt=0, lt=65536)
