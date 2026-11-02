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


def test_the_api_key_is_optional_so_the_cli_runs_without_it(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TICKETS__API_KEY", API_KEY)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.api.api_key is None and settings.api.port == 8001


def test_the_api_key_comes_from_the_environment_and_must_be_long_enough(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TICKETS__API_KEY", API_KEY)
    monkeypatch.setenv("AGENT_API__API_KEY", "agent-key-0123456789")
    assert Settings(_env_file=None).api.api_key.get_secret_value() == "agent-key-0123456789"  # type: ignore[call-arg, union-attr]

    monkeypatch.setenv("AGENT_API__API_KEY", "short")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_docker_config_differs_from_the_real_one_only_where_the_container_runs() -> None:
    # a tuning change (a model, a budget, a timeout) made in config.yaml alone would not reach the image
    local, docker = (yaml.safe_load(REAL_CONFIG.with_name(name).read_text()) for name in ("config.yaml", "config.docker.yaml"))
    for config in (local, docker):
        for section in config.values():
            for where in ("base_url", "url"):  # its neighbours, as the host or the compose network sees them
                section.pop(where, None)
        config["logs"].pop("path")  # the mounted folder
        config["api"].pop("host")  # 0.0.0.0 in a container
        config["api"].pop("port")

    assert docker == local


def test_the_docker_config_loads(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TICKETS__API_KEY", API_KEY)
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(REAL_CONFIG.with_name("config.docker.yaml")))

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.llm.model and settings.api.port
