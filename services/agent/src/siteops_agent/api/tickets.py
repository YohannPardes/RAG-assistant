from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from siteops_agent.api.errors import ApiError
from siteops_agent.drafts import DraftSigner, DraftTokenError
from siteops_agent.ticket_sink.base import TicketSink, TicketSinkError
from siteops_common.contracts import CreateTicketResult


class ConfirmTicket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_token: str = Field(min_length=1, max_length=64_000)  # only the token: the ticket's content can't be edited on the way


def router(sink: TicketSink | None, drafts: DraftSigner) -> APIRouter:
    routes = APIRouter()

    @routes.post("/tickets")
    def confirm(request: ConfirmTicket) -> CreateTicketResult:
        """File a drafted ticket. `created: false` means it was already open (the tickets service's dedup, agent/3)."""
        if sink is None:
            raise ApiError(503, "tickets_disabled", "no tickets service is configured")
        try:
            ticket = drafts.verify(request.draft_token)
        except DraftTokenError as error:
            raise ApiError(422, "invalid_draft", str(error)) from error
        try:
            return sink.create(ticket)
        except TicketSinkError as error:
            raise ApiError(502, "upstream_failed", f"tickets: {error}") from error

    return routes
