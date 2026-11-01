import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from siteops_rag.config import Settings

REAL_CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path) -> Path:
    """No RAG_* variable from the machine, and a YAML file of the test's own."""
    for name in list(os.environ):
        if name.startswith("RAG_"):
            monkeypatch.delenv(name)
    config_file = tmp_path / "config.yaml"
    monkeypatch.setenv("RAG_CONFIG_FILE", str(config_file))
    return config_file


def write_yaml(path: Path, overrides: dict) -> None:
    """The real config.yaml with `overrides` merged into its sections (every value is required)."""
    data = yaml.safe_load(REAL_CONFIG.read_text())
    for section, values in overrides.items():
        data[section] = {**data.get(section, {}), **values}
    path.write_text(yaml.safe_dump(data))


def test_no_file_is_an_error(isolated_env) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_missing_key_is_an_error(isolated_env) -> None:
    data = yaml.safe_load(REAL_CONFIG.read_text())
    del data["llm"]["temperature"]
    isolated_env.write_text(yaml.safe_dump(data))

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_top_k_defaults_to_10_without_a_retrieval_section(isolated_env) -> None:
    data = yaml.safe_load(REAL_CONFIG.read_text())
    del data["retrieval"]
    isolated_env.write_text(yaml.safe_dump(data))

    assert Settings(_env_file=None).retrieval.top_k == 10  # rag/7


def test_a_secret_comes_from_the_env_var_then_dotenv(isolated_env, tmp_path, monkeypatch) -> None:
    write_yaml(isolated_env, {})
    dotenv = tmp_path / ".env"
    dotenv.write_text("RAG_LLM__API_KEY=from-dotenv\n")

    assert Settings(_env_file=dotenv).llm.api_key.get_secret_value() == "from-dotenv"
    monkeypatch.setenv("RAG_LLM__API_KEY", "from-env")
    settings = Settings(_env_file=dotenv)
    assert settings.llm.api_key.get_secret_value() == "from-env"
    assert settings.llm.model == "qwen3.5:27b"  # the rest of the section still comes from the YAML


def test_a_setting_in_an_env_var_is_an_error(isolated_env, monkeypatch) -> None:
    write_yaml(isolated_env, {})
    monkeypatch.setenv("RAG_LLM__MODEL", "from-env")

    with pytest.raises(ValueError, match="llm.model"):
        Settings(_env_file=None)


def test_a_setting_in_dotenv_is_an_error(isolated_env, tmp_path) -> None:
    write_yaml(isolated_env, {})
    dotenv = tmp_path / ".env"
    dotenv.write_text("RAG_VECTOR_STORE__URL=http://elsewhere:6333\n")

    with pytest.raises(ValueError, match="vector_store.url"):
        Settings(_env_file=dotenv)


def test_a_secret_in_the_yaml_is_an_error(isolated_env) -> None:
    write_yaml(isolated_env, {"embedding": {"api_key": "s3cret"}})

    with pytest.raises(ValueError, match="embedding.api_key"):
        Settings(_env_file=None)


def test_misspelled_key_in_a_section_is_an_error(isolated_env) -> None:
    write_yaml(isolated_env, {"llm": {"modle": "qwen3.5:27b"}})

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("top_k", [0, 51])
def test_top_k_outside_what_a_request_may_ask_is_an_error(isolated_env, top_k) -> None:
    write_yaml(isolated_env, {"retrieval": {"top_k": top_k}})

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_values_are_hidden_when_printed(isolated_env, monkeypatch) -> None:
    write_yaml(isolated_env, {})
    monkeypatch.setenv("RAG_LLM__API_KEY", "s3cret")

    settings = Settings(_env_file=None)

    assert "s3cret" not in repr(settings)
    assert settings.llm.api_key.get_secret_value() == "s3cret"


def test_the_docker_config_differs_from_the_real_one_only_in_its_urls() -> None:
    # a tuning change made in config.yaml alone would not reach the image
    local, docker = (yaml.safe_load(REAL_CONFIG.with_name(name).read_text()) for name in ("config.yaml", "config.docker.yaml"))
    for config in (local, docker):
        for section in config.values():
            section.pop("base_url", None)
            section.pop("url", None)

    assert docker == local


def test_every_top_level_key_of_the_real_config_is_read() -> None:
    # Settings ignores unknown top-level keys (a shared .env may hold others), so a misplaced
    # section in config.yaml would be silently dropped and its defaults used instead
    keys = set(yaml.safe_load(REAL_CONFIG.read_text()))

    assert keys <= set(Settings.model_fields), f"not read by Settings: {sorted(keys - set(Settings.model_fields))}"


@pytest.mark.parametrize("name", ["config.yaml", "config.docker.yaml"])
def test_real_config_loads(monkeypatch, name) -> None:
    monkeypatch.setenv("RAG_CONFIG_FILE", str(REAL_CONFIG.with_name(name)))

    settings = Settings(_env_file=None)

    assert settings.llm.model
    assert settings.embedding.model
