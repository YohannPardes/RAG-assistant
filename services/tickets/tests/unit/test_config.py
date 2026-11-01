import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from siteops_tickets.config import Settings

SERVICE_DIR = Path(__file__).resolve().parents[2]
API_KEY = "test-key-0123456789"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path) -> Path:
    """No TICKETS_* variable from the machine, and a YAML file of the test's own (a copy of the real one)."""
    for name in list(os.environ):
        if name.startswith("TICKETS_"):
            monkeypatch.delenv(name)
    config_file = tmp_path / "config.yaml"
    config_file.write_text((SERVICE_DIR / "config.yaml").read_text())
    monkeypatch.setenv("TICKETS_CONFIG_FILE", str(config_file))
    return config_file


@pytest.mark.parametrize("name", ["config.yaml", "config.docker.yaml"])
def test_real_configs_load_with_the_key_from_the_environment(monkeypatch, name) -> None:
    monkeypatch.setenv("TICKETS_CONFIG_FILE", str(SERVICE_DIR / name))
    monkeypatch.setenv("TICKETS_API_KEY", API_KEY)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.api_key.get_secret_value() == API_KEY
    assert settings.dedup_minutes == 60  # agent/3


def test_the_key_comes_from_dotenv_too(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"TICKETS_API_KEY={API_KEY}\n")

    assert Settings(_env_file=dotenv).api_key.get_secret_value() == API_KEY  # type: ignore[call-arg]


def test_a_setting_in_an_env_var_is_an_error(monkeypatch) -> None:
    monkeypatch.setenv("TICKETS_API_KEY", API_KEY)
    monkeypatch.setenv("TICKETS_PORT", "9000")

    with pytest.raises(ValueError, match="port"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_key_in_the_yaml_is_an_error(isolated_env) -> None:
    data = yaml.safe_load(isolated_env.read_text()) | {"api_key": API_KEY}
    isolated_env.write_text(yaml.safe_dump(data))

    with pytest.raises(ValueError, match="api_key"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_a_setting_missing_from_the_yaml_is_an_error(isolated_env, monkeypatch) -> None:
    data = yaml.safe_load(isolated_env.read_text())
    del data["db_lock_timeout"]
    isolated_env.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("TICKETS_API_KEY", API_KEY)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
