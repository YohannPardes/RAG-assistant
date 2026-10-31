"""HTTP entry point of the rag service. Routes only: the work is done by objects built in container.py."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from siteops_common.contracts import AskRequest, AskResponse, SearchRequest, SearchResponse
from siteops_rag.container import build_rag
from siteops_rag.embedder.base import EmbeddingError
from siteops_rag.llm.base import LLMError
from siteops_rag.rag import RAG


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.rag = build_rag()  # once, at startup
    yield


app = FastAPI(title="siteops-rag", lifespan=lifespan)


def get_rag(request: Request) -> RAG:
    """Tests replace this dependency with a RAG built on fakes."""
    rag: RAG = request.app.state.rag
    return rag


@app.exception_handler(EmbeddingError)
@app.exception_handler(LLMError)
async def upstream_failed(request: Request, exc: Exception) -> JSONResponse:
    """A backend we call failed: 502, not a 500 that looks like our own bug."""
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check for docker-compose and the agent: the process is up."""
    return {"status": "ok"}


@app.post("/search")
def search(request: SearchRequest, rag: Annotated[RAG, Depends(get_rag)]) -> SearchResponse:
    """Embed the query, return the top-k chunks from the vector store, best first."""
    return rag.search_response(request.query, request.top_k)


@app.post("/ask")
def ask(request: AskRequest, rag: Annotated[RAG, Depends(get_rag)]) -> AskResponse:
    """Search, then answer from the retrieved chunks with citations, or say "I don't know"."""
    return rag.ask(request.question, request.top_k)
