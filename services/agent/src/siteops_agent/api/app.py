"""HTTP entry point of the agent: one router per area, assembled here. Routes only.

  health.py   GET  /health                   no key
  triage.py   POST /triage, GET /triage[/{run_id}]
  windows.py  GET  /windows                  the prefilter's windows for a range

The work is done by objects built in container.py and passed in as `ApiParts`. Every route but /health needs
`Authorization: Bearer <AGENT_API__API_KEY>`, and every error is `{"detail", "code"}` (errors.py).
Handlers are plain `def`: FastAPI runs them in a thread, off the event loop, so the synchronous graph (and the MCP
sink's own event loop) never run on the server's loop.
"""

from dataclasses import dataclass

from fastapi import FastAPI

from siteops_agent.api import errors, health, triage, windows
from siteops_agent.api.auth import ApiKeyMiddleware
from siteops_agent.runs import TriageRuns


@dataclass(frozen=True)
class ApiParts:
    """What the routes work with, built by the composition root."""

    runs: TriageRuns
    preview: windows.Preview
    api_key: str


def build_app(parts: ApiParts) -> FastAPI:
    app = FastAPI(title="siteops-agent")
    errors.install(app)
    app.add_middleware(ApiKeyMiddleware, api_key=parts.api_key)
    app.include_router(health.router())
    app.include_router(triage.router(parts.runs))
    app.include_router(windows.router(parts.preview))
    return app
