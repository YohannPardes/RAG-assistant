import time
import uuid

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from siteops_agent.api.errors import ApiError
from siteops_agent.chat.base import ChatEngine, ChatRequest, Citation, Step, UpstreamError, Warning
from siteops_agent.drafts import DraftSigner
from siteops_common.contracts import FaultType, Severity


class PendingTicket(BaseModel):
    """A drafted ticket for the user to confirm with POST /tickets {draft_token}. The token holds the ticket the
    code built, signed: the fields here are for display, the token is what gets filed (agent/15)."""

    model_config = ConfigDict(frozen=True)

    draft_token: str
    fault_type: FaultType
    host: str
    severity: Severity
    title: str
    summary: str
    suggested_fix: str
    evidence: list[int]  # citation numbers of this turn
    runbook: int | None


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn_id: str
    answered: bool
    answer: str  # its [n] markers are citations[].n; unchecked citations were dropped with their markers
    citations: list[Citation]
    steps: list[Step]
    pending_ticket: PendingTicket | None
    warnings: list[Warning]
    model: str
    latency_ms: float


def router(engine: ChatEngine, drafts: DraftSigner) -> APIRouter:
    routes = APIRouter()

    @routes.post("/chat")
    def chat(request: ChatRequest) -> ChatResponse:
        """One turn of the conversation: the history in, a cited answer out. Stateless: the client sends the history."""
        started = time.perf_counter()
        try:
            turn = engine.reply(request)
        except UpstreamError as error:
            status, code = (504, "upstream_timeout") if error.timeout else (502, "upstream_failed")
            raise ApiError(status, code, str(error)) from error

        pending = None
        if request.draft_ticket and turn.proposal is not None:  # a proposal on any other turn is ignored
            ticket = turn.proposal.ticket
            pending = PendingTicket(
                draft_token=drafts.sign(ticket),
                fault_type=ticket.fault_type,
                host=ticket.host,
                severity=ticket.severity,
                title=ticket.title,
                summary=ticket.summary,
                suggested_fix=ticket.suggested_fix,
                evidence=turn.proposal.evidence,
                runbook=turn.proposal.runbook,
            )
        return ChatResponse(
            turn_id=uuid.uuid4().hex[:12],
            answered=turn.answered,
            answer=turn.answer,
            citations=turn.citations,
            steps=turn.steps,
            pending_ticket=pending,
            warnings=turn.warnings,
            model=turn.model,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    return routes
