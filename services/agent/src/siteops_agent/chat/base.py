"""The chat engine's contract (agent/15): one turn in, one checked turn out. The engine itself (agent/11) is not here.

The models enforce what the frontend relies on, so an engine bug fails here instead of on the user's screen:
every `[n]` marker of the answer is a citation, quote offsets lie inside their chunk, and a ticket proposal only
points at citations of the same turn.
"""

import re
from abc import ABC, abstractmethod
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from siteops_agent.models import Level
from siteops_common.contracts import TicketCreate

MAX_MESSAGES = 40  # the history a turn carries: older turns are the frontend's to drop
MAX_MESSAGE_CHARS = 8000
MARKER = re.compile(r"\[(\d+)\]")  # a citation marker in the answer: "[3]"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatMessage(_Model):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(_Model):
    """One turn: the whole history (stateless, agent/8), the log range in view, and whether to draft a ticket."""

    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    since: AwareDatetime
    until: AwareDatetime
    draft_ticket: bool = False  # the user clicked "Write a ticket"

    @model_validator(mode="after")
    def _well_formed(self) -> Self:
        if self.messages[-1].role != "user":
            raise ValueError("the last message must be the user's")
        if self.until <= self.since:
            raise ValueError("until must be after since")
        return self


class RunbookCitation(_Model):
    n: int = Field(ge=1)
    kind: Literal["runbook"] = "runbook"
    chunk_id: str
    source: str
    heading_path: list[str]
    page: int | None
    quote: str
    chunk_text: str
    quote_start: int = Field(ge=0)  # the quote's span in chunk_text, found by the backend's whitespace-blind check
    quote_end: int

    @model_validator(mode="after")
    def _quote_inside_chunk(self) -> Self:
        if not self.quote_start <= self.quote_end <= len(self.chunk_text):
            raise ValueError(f"citation [{self.n}]: quote offsets outside the chunk")
        return self


class ContextLine(_Model):
    raw: str
    cited: bool


class LogCitation(_Model):
    n: int = Field(ge=1)
    kind: Literal["log"] = "log"
    host: str
    time: AwareDatetime
    level: Level
    component: str
    raw: str
    context: list[ContextLine]  # the lines the model saw around it, the cited one flagged


Citation = Annotated[RunbookCitation | LogCitation, Field(discriminator="kind")]


class Hit(_Model):
    chunk_id: str
    source: str
    heading_path: list[str]
    score: float
    cited: bool


class Step(_Model):
    """One tool call of the turn, for the steps list."""

    tool: str
    args: dict[str, object]
    ok: bool
    summary: str  # "10 hits", "5 lines", or why it failed
    hits: list[Hit] | None = None  # search_runbooks only: feeds the "I don't know" card


class Warning(_Model):  # noqa: A001  # the frontend's name for a degraded turn
    service: Literal["rag", "logs", "tickets", "model"]
    message: str


class TicketProposal(_Model):
    """A ticket the engine drafted (draft_ticket turns only): built by code from checked judgement, as in triage
    (agent/10). `evidence` and `runbook` are citation numbers of the same turn, so the card reuses its sources."""

    ticket: TicketCreate
    evidence: list[int] = Field(min_length=1)
    runbook: int | None = None


class ChatTurn(_Model):
    """What the engine returns for one turn."""

    answered: bool  # False: "I don't know" (the steps' hits show the closest sections)
    answer: str
    citations: list[Citation]
    steps: list[Step]
    warnings: list[Warning] = Field(default_factory=list)
    proposal: TicketProposal | None = None
    model: str

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        numbers = [citation.n for citation in self.citations]
        if len(set(numbers)) != len(numbers):
            raise ValueError("citation numbers must be unique")
        if unknown := sorted({int(n) for n in MARKER.findall(self.answer)} - set(numbers)):
            raise ValueError(f"the answer cites {unknown}, which are not citations")
        if self.proposal is not None:
            kinds = {citation.n: citation.kind for citation in self.citations}
            if any(kinds.get(n) != "log" for n in self.proposal.evidence):
                raise ValueError("a proposal's evidence must be log citations of the turn")
            if self.proposal.runbook is not None and kinds.get(self.proposal.runbook) != "runbook":
                raise ValueError("a proposal's runbook must be a runbook citation of the turn")
        return self


class UpstreamError(Exception):
    """A service the turn needs failed: the model always; rag only when the turn can't do without it.
    (A turn that can go on without rag answers with a warning instead.)"""

    def __init__(self, service: str, message: str, timeout: bool = False) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service
        self.timeout = timeout  # True: 504 (slow), False: 502 (down or broken)


class ChatEngine(ABC):
    """Answers one chat turn with read-only tools (agent/11); never files a ticket itself."""

    @abstractmethod
    def reply(self, request: ChatRequest) -> ChatTurn:
        """The checked turn. Raises UpstreamError when a service it needs fails."""
