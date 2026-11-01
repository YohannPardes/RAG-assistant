"""MCP entry point of the tickets service: transport and wiring only. The tools themselves are in tools.py.

Streamable HTTP (arch/6) at /mcp, behind an API key (agent/5).
Run: TICKETS_API_KEY=... uv run python -m siteops_tickets.server
"""

import hmac
from datetime import timedelta

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from siteops_tickets.config import Settings
from siteops_tickets.store import TicketStore
from siteops_tickets.tools import TicketTools


def build_server(store: TicketStore) -> MCPServer:
    """The MCP server: the tools of tools.py, plus /health."""
    server = MCPServer("siteops-tickets", instructions="Opens deduplicated incident tickets.")
    TicketTools(store).register(server)

    @server.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]  # the SDK leaves it untyped
    async def health(request: Request) -> Response:
        """Liveness check for docker-compose: the process is up. Needs no key."""
        return JSONResponse({"status": "ok"})

    return server


class ApiKeyMiddleware:
    """Every HTTP request but /health must carry `Authorization: Bearer <key>`. Lifespan events pass through."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self._expected = f"Bearer {api_key}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/health" and not self._authorized(scope):
            await JSONResponse({"detail": "missing or invalid API key"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        given = Headers(scope=scope).get("authorization", "").encode()
        return hmac.compare_digest(given, self._expected)  # constant time: the response time doesn't leak the key


def build_app(settings: Settings) -> ASGIApp:
    """Composition root: the store, the MCP server, the HTTP app and its key check."""
    store = TicketStore(settings.db_path, timedelta(minutes=settings.dedup_minutes))
    # stateless + JSON: no session to keep between calls and no SSE stream, as one tool call is one request
    http = build_server(store).streamable_http_app(stateless_http=True, json_response=True, host=settings.host)
    return ApiKeyMiddleware(http, settings.api_key.get_secret_value())


def main() -> None:
    settings = Settings()  # api_key comes from the environment
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
