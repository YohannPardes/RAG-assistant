"""Wire contracts shared between services (arch/4). Pydantic models only, no logic."""

from pydantic import BaseModel, ConfigDict, Field


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
