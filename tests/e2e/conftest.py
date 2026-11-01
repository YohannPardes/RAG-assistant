"""The real services, in-process: the agent's clients against the tickets and rag apps, without containers."""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import uvicorn
from starlette.types import ASGIApp

from siteops_tickets.config import Settings
from siteops_tickets.server import build_app

API_KEY = "e2e-key-0123456789"


@contextmanager
def serve(app: ASGIApp) -> Iterator[str]:
    """`app` on a free local port, in a thread: its base URL. Real HTTP, as between the containers."""
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


@pytest.fixture
def tickets_url(tmp_path) -> Iterator[str]:
    """The tickets service on a fresh database: its MCP endpoint."""
    # 0.0.0.0: no localhost-only Host check, as in the tickets container (the test client sends 127.0.0.1:<port>)
    settings = Settings(api_key=API_KEY, db_path=tmp_path / "tickets.db", host="0.0.0.0")
    with serve(build_app(settings)) as base_url:
        yield f"{base_url}/mcp"
