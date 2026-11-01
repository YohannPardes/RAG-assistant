from datetime import timedelta
from typing import Any

import anyio
import pytest
from mcp import Client
from starlette.testclient import TestClient

from siteops_common.contracts import CreateTicketResult
from siteops_tickets.config import Settings
from siteops_tickets.server import build_app, build_server
from siteops_tickets.store import TicketStore

API_KEY = "test-key-0123456789"
MCP_HEADERS = {"accept": "application/json, text/event-stream"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def call(server, name: str, arguments: dict[str, Any] | None = None):
    """One MCP request through an in-process client (no HTTP): tests the tool, not the transport."""

    async def run():
        async with Client(server) as client:
            return await (client.call_tool(name, arguments) if arguments is not None else client.list_tools())

    return anyio.run(run)


@pytest.fixture
def store(tmp_path) -> TicketStore:
    return TicketStore(tmp_path / "tickets.db", dedup_window=timedelta(minutes=60), lock_timeout=10.0)


@pytest.fixture
def server(store):
    return build_server(store)


@pytest.fixture
def http(tmp_path):
    app = build_app(Settings(api_key=API_KEY, db_path=tmp_path / "tickets.db"))
    # localhost: the bind host is 127.0.0.1, so the SDK only accepts a localhost Host header
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client


def test_the_only_tool_is_create_ticket_with_the_contract_schema(server) -> None:
    tools = call(server, "list").tools

    assert [tool.name for tool in tools] == ["create_ticket"]
    # generated from TicketCreate (arch/4): the agent's LLM sees every field, the suggested fix included
    assert "suggested_fix" in str(tools[0].input_schema) and tools[0].output_schema
    # the description is the method's docstring, without its source indentation
    assert tools[0].description.startswith("Open an incident ticket") and "\n " not in tools[0].description


def test_create_ticket_returns_the_stored_ticket_then_deduplicates(server, ticket_payload) -> None:
    first = CreateTicketResult.model_validate(call(server, "create_ticket", {"ticket": ticket_payload}).structured_content)
    again = CreateTicketResult.model_validate(call(server, "create_ticket", {"ticket": ticket_payload}).structured_content)

    assert first.created and not again.created
    assert again.ticket.id == first.ticket.id


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"fault_type": "overheating"}, "fault_type"),
        ({"evidence": []}, "evidence"),
        ({"window_start": "2026-11-01T07:03:06"}, "window_start"),  # no zone
    ],
    ids=["unknown-fault-type", "no-evidence", "naive-time"],
)
def test_an_invalid_ticket_is_a_tool_error_and_nothing_is_stored(server, store, ticket, ticket_payload, change, field) -> None:
    # the agent gets the validation message back and can correct its call
    result = call(server, "create_ticket", {"ticket": ticket_payload | change})

    assert result.is_error
    assert field in str(result.content)
    assert store.create(ticket).created  # the first valid ticket is new: the invalid one left nothing behind


def test_health_needs_no_key(http) -> None:
    assert http.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": "Bearer wrong-key-0123456789"},
        {"authorization": f"Bearer {API_KEY[:-1]}X"},  # same length, last character differs: what compare_digest is for
        {"authorization": API_KEY},
        {"authorization": "Bearer "},  # an empty key never matches: Settings refuses to start with one
    ],
    ids=["no-key", "wrong-key", "same-length-wrong-key", "no-bearer-prefix", "empty-key"],
)
def test_mcp_without_the_right_key_is_refused(http, headers) -> None:
    assert http.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | headers).status_code == 401


def test_mcp_with_the_key_answers(http) -> None:
    response = http.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | {"authorization": f"Bearer {API_KEY}"})

    assert response.status_code == 200
    assert response.json()["result"]["tools"][0]["name"] == "create_ticket"


def test_the_service_refuses_to_start_without_a_long_enough_key(monkeypatch) -> None:
    monkeypatch.delenv("TICKETS_API_KEY", raising=False)

    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(api_key="short", _env_file=None)
