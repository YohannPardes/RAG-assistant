"""The docker stack's values that live in several files agree: a port, a service name or a path changed in one of
them alone would build fine and fail only once the stack runs (a service never healthy, a neighbour unreachable)."""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
HEALTH_URL = re.compile(r"http://localhost:(\d+)/health")


def docker_config(service: str) -> dict:
    return yaml.safe_load((ROOT / "services" / service / "config.docker.yaml").read_text(encoding="utf-8"))


def dockerfile(service: str) -> str:
    return (ROOT / "services" / service / "Dockerfile").read_text(encoding="utf-8")


def listens_on(service: str) -> int:
    """The port the service binds inside its container: its config, or rag's uvicorn command."""
    if service == "rag":
        return int(re.search(r'"--port", "(\d+)"', dockerfile("rag")).group(1))  # type: ignore[union-attr]
    config = docker_config(service)
    return int((config["api"] if service == "agent" else config)["port"])


def container_ports(service: str) -> set[int]:
    """The container side of the service's port mappings in compose ("127.0.0.1:${PORT:-8000}:8000" → 8000)."""
    return {int(mapping.rsplit(":", 1)[1]) for mapping in SERVICES[service].get("ports", [])}


def health_port(service: str) -> int:
    return int(HEALTH_URL.search(" ".join(SERVICES[service]["healthcheck"]["test"])).group(1))  # type: ignore[union-attr]


def reaches(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname or "", parts.port or 80


@pytest.mark.parametrize("service", ["rag", "tickets", "agent"])
def test_each_service_listens_where_its_image_compose_and_health_check_say(service) -> None:
    port = listens_on(service)

    assert f"EXPOSE {port}" in dockerfile(service)
    assert container_ports(service) == {port}
    assert health_port(service) == port


def test_the_agent_reaches_rag_and_tickets_where_they_listen() -> None:
    config = docker_config("agent")

    assert reaches(config["rag"]["base_url"]) == ("rag", listens_on("rag"))
    assert reaches(config["tickets"]["url"]) == ("tickets", listens_on("tickets"))
    assert urlsplit(config["tickets"]["url"]).path == "/mcp"


def test_rag_reaches_qdrant_where_it_listens() -> None:
    assert reaches(docker_config("rag")["vector_store"]["url"]) == ("qdrant", *container_ports("qdrant"))


def test_rag_and_the_agent_reach_the_model_server_by_its_alias_and_port() -> None:
    ollama = SERVICES["ollama"]
    alias = set(ollama["networks"]["default"]["aliases"])
    listen = int(re.match(r"tcp-listen:(\d+)", ollama["command"][0]).group(1))  # type: ignore[union-attr]
    rag, agent = docker_config("rag"), docker_config("agent")

    for url in (rag["embedding"]["base_url"], rag["llm"]["base_url"], agent["llm"]["base_url"]):
        host, port = reaches(url)
        assert host in alias and port == listen
        assert host.endswith(".internal")  # a host Ollama refuses requests for a plain name like "ollama" (403)


def test_the_agent_reads_its_logs_from_the_mounted_folder() -> None:
    logs = Path(docker_config("agent")["logs"]["path"])
    mounted = [volume.split(":")[1] for volume in SERVICES["agent"]["volumes"]]

    assert any(logs.is_relative_to(target) for target in mounted)


def test_tickets_keeps_its_database_on_its_volume() -> None:
    database = Path(docker_config("tickets")["db_path"])
    volumes = [volume.split(":")[1] for volume in SERVICES["tickets"]["volumes"] if not volume.startswith(".")]

    assert any(database.is_relative_to(target) for target in volumes)


def test_ingest_finds_the_data_and_the_script_where_compose_mounts_them() -> None:
    ingest = SERVICES["ingest"]
    targets = {volume.split(":")[1] for volume in ingest["volumes"]}

    assert {"/app/data", "/app/scripts"} <= targets  # the script's default folder is <script>/../data
    assert ingest["command"][1:] == ["scripts/ingest.py"]  # no folder: the default, data/


def test_the_makefile_pulls_the_models_the_configs_use() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    pulled = set(re.search(r"^MODELS := (.+)$", makefile, re.MULTILINE).group(1).split())  # type: ignore[union-attr]
    rag, agent = docker_config("rag"), docker_config("agent")

    assert {rag["embedding"]["model"], rag["llm"]["model"], agent["llm"]["model"]} <= pulled
