"""Wire contracts shared between services (arch/4). Pydantic models only, no logic."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class _Contract(BaseModel):
    """Base for every contract: unknown fields are errors, and a received message can't be changed."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# rag: POST /search


class SearchRequest(_Contract):
    """A query to retrieve runbook chunks for."""

    query: str = Field(min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=50)  # None: the rag service's configured default (rag/7)


class SearchHit(_Contract):
    """One retrieved chunk and its score."""

    chunk_id: str
    source: str  # e.g. "runbook_disk_full.pdf"
    heading_path: list[str]
    page: int | None
    text: str
    score: float  # cosine similarity: higher = more similar (rag/13)


class SearchResponse(_Contract):
    """The hits, best first."""

    hits: list[SearchHit]
    embedding_model: str  # which model produced the scores, so an eval knows what it measured
    latency_ms: float  # time spent serving the request


# rag: POST /ask


class AskRequest(_Contract):
    """A question to answer from the runbooks."""

    question: str = Field(min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=50)  # chunks the LLM gets as context; None: the service default


class Citation(_Contract):
    """Where a part of the answer comes from (rag/10)."""

    chunk_id: str  # lets a caller check the citation was really retrieved
    source: str
    heading_path: list[str]
    page: int | None
    quote: str  # the passage of the chunk the answer relies on: checkable against the chunk's text


class AskResponse(_Contract):
    """The answer with its citations, or "I don't know" (rag/9)."""

    answered: bool  # False = "I don't know": callers check this, not the answer text
    answer: str
    citations: list[Citation]
    hits: list[SearchHit]  # the context the LLM saw: tells "not retrieved" apart from "retrieved but ignored"
    model: str  # the LLM that answered (rag/4)
    embedding_model: str
    latency_ms: float  # time spent serving the request, retrieval and generation included


# tickets: MCP tool create_ticket (arch/6)

# What the agent may diagnose: the seeded faults (scope/2) and "other" for a real problem none of them fits.
# One list for the agent's prompt, the tickets service and the eval's matching rule (scope/4).
FaultType = Literal[
    "disk_full",
    "pg_primary_down",
    "pg_replica_lag",
    "cert_expiry",
    "high_latency",
    "api_error_rate",
    "pod_oomkilled",
    "other",
]

Severity = Literal["SEV-1", "SEV-2", "SEV-3"]  # the runbooks' scale: SEV-1 is the worst

EvidenceLine = Annotated[str, Field(min_length=1, max_length=2000)]


class RunbookRef(_Contract):
    """The runbook section a ticket relies on: a chunk rag /search returned to the agent."""

    chunk_id: str = Field(min_length=1)  # lets the agent check it really was retrieved
    source: str = Field(min_length=1)  # e.g. "runbook_disk_full.pdf": the eval compares it to `expected_runbook`
    heading_path: list[str]


class TicketCreate(_Contract):
    """An incident the agent asks to open.

    Every text field is bounded: the agent writes them from untrusted logs (agent/1), so a prompt-injected
    agent can't fill the store or a reader's screen.
    """

    fault_type: FaultType
    host: str = Field(min_length=1, max_length=255)  # the root cause, which may not be the host the symptoms came from
    severity: Severity
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)  # the incident report: what happened, impact, why this diagnosis
    suggested_fix: str = Field(min_length=1, max_length=4000)  # the agent's proposed remediation, for a human to apply: never executed
    evidence: list[EvidenceLine] = Field(min_length=1, max_length=20)  # raw log lines, quoted unchanged
    runbook: RunbookRef | None = None  # None when no runbook fits, e.g. fault_type "other"
    window_start: AwareDatetime  # the prefilter window the diagnosis comes from
    window_end: AwareDatetime

    @model_validator(mode="after")
    def _window_in_order(self) -> Self:
        if self.window_end < self.window_start:
            raise ValueError("window_end is before window_start")
        return self


class Ticket(TicketCreate):
    """A stored ticket: what the agent sent, plus what the tickets service added."""

    id: str = Field(min_length=1)
    created_at: AwareDatetime
    fingerprint: str = Field(min_length=1)  # "the same fault": tickets with one fingerprint are deduplicated (agent/3)


class CreateTicketResult(_Contract):
    """The reply to create_ticket: the stored ticket, new or the one it duplicates."""

    ticket: Ticket
    created: bool  # False = deduplicated: a ticket with this fingerprint is still within the dedup window (agent/3)
