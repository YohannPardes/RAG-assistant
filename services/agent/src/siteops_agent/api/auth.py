import hmac

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from siteops_agent.api.errors import body


class ApiKeyMiddleware:
    """Every HTTP request but /health must carry `Authorization: Bearer <key>` (agent/5); compared in constant time."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self._expected = f"Bearer {api_key}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/health":
            given = Headers(scope=scope).get("authorization", "").encode()
            if not hmac.compare_digest(given, self._expected):
                await JSONResponse(body("unauthorized", "missing or invalid API key"), status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)
