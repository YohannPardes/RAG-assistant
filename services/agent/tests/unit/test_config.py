import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from siteops_agent.config import Settings

REAL_CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"
API_KEY = "test-key-0123456789"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path) -> Path:
    """No AGENT_* variable from the machine, and a YAML file of the test's own (a copy of the real one)."""
    for name in list(os.environ):
        if name.startswith("AGENT_"):
            monkeypatch.delenv(name)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(REAL_CONFIG.read_text())
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(config_file))
    return config_file


def test_real_config_loads_with_the_key_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TICKETS__API_KEY", API_KEY)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.tickets.api_key.get_secret_value() == API_KEY
    assert settings.rag.base_url


def test_the_key_comes_from_dotenv_too(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"AGENT_TICKETS__API_KEY={API_KEY}\n")

    assert Settings(_env_file=dotenv).tickets.api_key.get_secret_value() == API_KEY  # type: ignore[call-arg]


def test_a_setting_in_an_env_var_is_an_error(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TICKETS__API_KEY", API_KEY)
    monkeypatch.setenv("AGENT_RAG__BASE_URL", "http://rag:8000")

    with pytest.raises(ValueError, match="rag.base_url"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_a_secret_in_the_yaml_is_an_error(isolated_env) -> None:
    data = yaml.safe_load(isolated_env.read_text())
    data["tickets"]["api_key"] = API_KEY
    isolated_env.write_text(yaml.safe_dump(data))

    with pytest.raises(ValueError, match="tickets.api_key"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_no_key_is_an_error() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
