"""HTTP entry point of the agent (agent/8): start a triage run, follow it, preview its windows. Routes only.

The work is done by objects built in container.py: `TriageRuns` (the same job as the CLI's) and the window preview.
Every route but /health needs `Authorization: Bearer <AGENT_API__API_KEY>` (agent/5).
"""

import hmac
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from siteops_agent.models import Window
from siteops_agent.runs import QueueFull, RunStatus, TriageRequest, TriageRuns

Preview = Callable[[datetime, datetime], list[Window]]  # the windows a run of [since, until) would triage


class WindowSummary(BaseModel):
    """A window without its events: what /windows lists."""

    model_config = ConfigDict(frozen=True)

    window_id: str
    host: str
    rule: str
    start: datetime
    end: datetime
    events: int
    omitted: int

    @classmethod
    def of(cls, window: Window) -> "WindowSummary":
        return cls(
            window_id=window.id,
            host=window.host,
            rule=window.rule,
            start=window.start,
            end=window.end,
            events=len(window.events),
            omitted=window.omitted,
        )


class ApiKeyMiddleware:
    """Every HTTP request but /health must carry `Authorization: Bearer <key>`; compared in constant time."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self._expected = f"Bearer {api_key}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/health":
            given = Headers(scope=scope).get("authorization", "").encode()
            if not hmac.compare_digest(given, self._expected):
                await JSONResponse({"detail": "missing or invalid API key"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(runs: TriageRuns, preview: Preview, api_key: str) -> FastAPI:
    """The API over `runs` and `preview`. Handlers are plain `def`: FastAPI runs them in a thread, off the event loop."""
    app = FastAPI(title="siteops-agent")
    app.add_middleware(ApiKeyMiddleware, api_key=api_key)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/triage", status_code=202)
    def start_triage(request: TriageRequest) -> RunStatus:
        """Queue a run of [since, until) and return at once; follow it with GET /triage/{run_id}.
        503 when the queue is full: the server is at capacity for now, not the request at fault."""
        try:
            return runs.start(request)
        except QueueFull as error:
            raise HTTPException(status_code=503, detail=str(error), headers={"Retry-After": "60"}) from error

    @app.get("/triage")
    def list_runs() -> list[RunStatus]:
        """The recent runs, newest first, without their reports."""
        return [run.model_copy(update={"report": None}) for run in runs.recent()]

    @app.get("/triage/{run_id}")
    def get_run(run_id: str) -> RunStatus:
        if (run := runs.get(run_id)) is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id}")
        return run

    @app.get("/windows")
    def windows(since: Annotated[AwareDatetime, Query()], until: Annotated[AwareDatetime, Query()]) -> list[WindowSummary]:
        """The windows a run of [since, until) would triage: the prefilter only, no LLM, nothing filed."""
        if until <= since:
            raise HTTPException(status_code=422, detail="until must be after since")
        return [WindowSummary.of(window) for window in preview(since, until)]

    return app
