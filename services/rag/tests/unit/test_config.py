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


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data))


def test_defaults_apply_when_there_is_no_file(isolated_env) -> None:
    settings = Settings(_env_file=None)

    assert settings.embedding.dimension == 1024
    assert settings.chunking.max_length == 1000
    assert settings.vector_store.backend == "qdrant"


def test_env_var_beats_dotenv_which_beats_yaml(isolated_env, tmp_path, monkeypatch) -> None:
    write_yaml(isolated_env, {"llm": {"model": "from-yaml", "temperature": 0.5}})
    dotenv = tmp_path / ".env"
    dotenv.write_text("RAG_LLM__MODEL=from-dotenv\n")

    assert Settings(_env_file=None).llm.model == "from-yaml"
    assert Settings(_env_file=dotenv).llm.model == "from-dotenv"
    monkeypatch.setenv("RAG_LLM__MODEL", "from-env")
    settings = Settings(_env_file=dotenv)
    assert settings.llm.model == "from-env"
    assert settings.llm.temperature == 0.5  # sources merge per field: the YAML's other values stay


def test_misspelled_key_in_a_section_is_an_error(isolated_env) -> None:
    write_yaml(isolated_env, {"llm": {"modle": "qwen3.5:27b"}})

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_values_are_hidden_when_printed(isolated_env) -> None:
    write_yaml(isolated_env, {"llm": {"api_key": "s3cret"}})

    settings = Settings(_env_file=None)

    assert "s3cret" not in repr(settings)
    assert settings.llm.api_key.get_secret_value() == "s3cret"


def test_every_top_level_key_of_the_real_config_is_read() -> None:
    # Settings ignores unknown top-level keys (a shared .env may hold others), so a misplaced
    # section in config.yaml would be silently dropped and its defaults used instead
    keys = set(yaml.safe_load(REAL_CONFIG.read_text()))

    assert keys <= set(Settings.model_fields), f"not read by Settings: {sorted(keys - set(Settings.model_fields))}"


def test_real_config_loads(monkeypatch) -> None:
    monkeypatch.setenv("RAG_CONFIG_FILE", str(REAL_CONFIG))

    settings = Settings(_env_file=None)

    assert settings.llm.model  # the default is empty: the real config must name a model
    assert settings.embedding.model
