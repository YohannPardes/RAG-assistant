"""A stand-in tickets MCP server on a local port: the MCP sink's tests go over real HTTP, without the tickets service.

It serves the same tool (create_ticket, TicketCreate → CreateTicketResult) behind the same kind of key check, and
files into a MemoryTicketSink with a clock the tests move. The real service is checked in tests/e2e.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from siteops_agent.ticket_sink.memory import MemoryTicketSink
from siteops_common.contracts import CreateTicketResult, TicketCreate

API_KEY = "test-key-0123456789"
NOW = datetime(2026, 11, 1, 8, tzinfo=UTC)


@dataclass
class Backend:
    """What the stand-in server files into; reset for every test."""

    now: datetime = NOW
    refuse: str | None = None  # set: the tool fails with this message, as a tickets service refusing a ticket would
    sink: MemoryTicketSink = field(init=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.now, self.refuse = NOW, None
        self.sink = MemoryTicketSink(now=lambda: self.now)

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def stand_in_app(backend: Backend) -> ASGIApp:
    server = MCPServer("stand-in-tickets")

    def create_ticket(ticket: TicketCreate) -> CreateTicketResult:
        """Open an incident ticket."""
        if backend.refuse is not None:
            raise ToolError(backend.refuse)  # an anticipated failure: the client gets its message
        return backend.sink.create(ticket)

    server.add_tool(create_ticket)
    # 0.0.0.0: no localhost-only Host check, as in the tickets container
    mcp_app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != f"Bearer {API_KEY}".encode():
            await JSONResponse({"detail": "missing or invalid API key"}, status_code=401)(scope, receive, send)
            return
        await mcp_app(scope, receive, send)

    return app


@contextmanager
def serve(app: ASGIApp) -> Iterator[str]:
    """`app` on a free local port, in a thread: its base URL."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the test server did not start")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="module")
def stand_in() -> Iterator[tuple[Backend, str]]:
    backend = Backend()
    with serve(stand_in_app(backend)) as base_url:
        yield backend, f"{base_url}/mcp"


@pytest.fixture
def backend(stand_in) -> Backend:
    backend, _ = stand_in
    backend.reset()
    return backend


@pytest.fixture
def mcp_url(stand_in, backend) -> str:
    return stand_in[1]
