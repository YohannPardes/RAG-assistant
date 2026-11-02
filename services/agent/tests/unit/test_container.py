"""The composition root on the real config.yaml: the apps build without any service running, and wire what they should."""

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from siteops_agent.config import Settings
from siteops_agent.container import build_api, build_job

REAL_CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"
API_KEY = "agent-key-0123456789"
AUTH = {"authorization": f"Bearer {API_KEY}"}
RANGE = {"since": "2026-11-01T07:00:00Z", "until": "2026-11-01T08:00:00Z"}
LINES = [
    "2026-11-01T07:03:06.000Z WARN log-02 disk-monitor: /var/log usage 88%",
    "2026-11-01T07:09:07.000Z ERROR log-02 rsyslogd: write error on /var/log/remote/app: No space left on device",
]


@pytest.fixture(autouse=True)
def config(monkeypatch, tmp_path) -> Path:
    """The real config.yaml, its log file swapped for a small one of the test's own; the secrets in env vars."""
    for name in list(os.environ):
        if name.startswith("AGENT_"):
            monkeypatch.delenv(name)
    log_file = tmp_path / "site.log"
    log_file.write_text("\n".join(LINES) + "\n")
    data = yaml.safe_load(REAL_CONFIG.read_text())
    data["logs"]["path"] = str(log_file)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("AGENT_TICKETS__API_KEY", "tickets-key-0123456789")
    monkeypatch.setenv("AGENT_API__API_KEY", API_KEY)
    return log_file


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_api(settings()))


def test_health_needs_no_key_and_the_rest_does(client) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/windows", params=RANGE).status_code == 401
    assert client.get("/triage", headers=AUTH).json() == []  # no run yet


def test_the_preview_reads_the_configured_log_file(client) -> None:
    windows = client.get("/windows", params=RANGE, headers=AUTH).json()

    assert [(window["host"], window["rule"]) for window in windows] == [("log-02", "disk_pressure")]


def test_the_api_refuses_to_build_without_its_key(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_API__API_KEY")

    with pytest.raises(ValueError, match="AGENT_API__API_KEY"):
        build_api(settings())


def test_the_triage_job_needs_no_api_key(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_API__API_KEY")

    assert callable(build_job(settings()))
