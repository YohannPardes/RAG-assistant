"""HTTP entry point of the agent (agent/8, agent/15): one router per area, assembled here. Routes only.

  health.py   GET  /health                   no key
  meta.py     GET  /meta                     what the frontend offers and hides
  chat.py     POST /chat                     one conversation turn
  tickets.py  POST /tickets                  confirm a drafted ticket (a signed draft_token)
  triage.py   POST /triage, GET /triage[/{run_id}]
  windows.py  GET  /windows                  the prefilter's windows for a range

The work is done by objects built in container.py and passed in as `ApiParts`. Every route but /health needs
`Authorization: Bearer <AGENT_API__API_KEY>` (agent/5), and every error is `{"detail", "code"}` (errors.py).
Handlers are plain `def`: FastAPI runs them in a thread, off the event loop, so the synchronous engine and graph
(and the MCP sink's own event loop) never run on the server's loop.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI

from siteops_agent.api import chat, errors, health, meta, tickets, triage, windows
from siteops_agent.api.auth import ApiKeyMiddleware
from siteops_agent.chat.base import ChatEngine
from siteops_agent.drafts import DraftSigner
from siteops_agent.runs import TriageRuns
from siteops_agent.ticket_sink.base import TicketSink


@dataclass(frozen=True)
class ApiParts:
    """What the routes work with, built by the composition root."""

    runs: TriageRuns
    preview: windows.Preview
    engine: ChatEngine
    drafts: DraftSigner
    sink: TicketSink | None  # None: no tickets service configured (meta's tickets_enabled is False)
    meta: Callable[[], meta.Meta]
    api_key: str


def build_app(parts: ApiParts) -> FastAPI:
    app = FastAPI(title="siteops-agent")
    errors.install(app)
    app.add_middleware(ApiKeyMiddleware, api_key=parts.api_key)
    app.include_router(health.router())
    app.include_router(meta.router(parts.meta))
    app.include_router(chat.router(parts.engine, parts.drafts))
    app.include_router(tickets.router(parts.sink, parts.drafts))
    app.include_router(triage.router(parts.runs))
    app.include_router(windows.router(parts.preview))
    return app
