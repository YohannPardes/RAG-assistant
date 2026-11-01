"""One error shape for every route (agent/15): `{"detail": "…", "code": "…"}`.

`code` is stable and machine-readable (the frontend branches on it); `detail` is for people. 422: the request is
invalid; 502 / 504: a service we depend on is down or slow (arch/7); 503: we are at capacity.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail, self.headers = status, code, detail, headers


def body(code: str, detail: str) -> dict[str, str]:
    return {"detail": detail, "code": code}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(body(error.code, error.detail), status_code=error.status, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(body("invalid_request", _describe(error.errors())), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:  # unknown route, wrong method
        return JSONResponse(body(f"http_{error.status_code}", str(error.detail)), status_code=error.status_code)


def _describe(errors: Any) -> str:
    """Pydantic's errors as one line the frontend can show next to the input: "body.until: until must be after since"."""
    parts = []
    for error in errors:
        location = ".".join(str(part) for part in error.get("loc", ()))
        parts.append(f"{location}: {str(error.get('msg', '')).removeprefix('Value error, ')}")
    return "; ".join(parts)
