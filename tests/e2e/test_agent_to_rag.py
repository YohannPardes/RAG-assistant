"""The agent's HTTP rag client against the real rag API (routes, contracts, error status), on fake backends."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from siteops_agent.rag_client.base import RagError
from siteops_agent.rag_client.http import HttpRagClient
from siteops_common.contracts import SearchRequest
from siteops_rag.api import app, get_rag
from siteops_rag.embedder.base import Embedder, EmbeddingError, Vector
from siteops_rag.embedder.fake import FakeEmbedder
from siteops_rag.llm.fake import FakeChatModel
from siteops_rag.models import Chunk
from siteops_rag.rag import RAG
from siteops_rag.vector_store.memory import InMemoryVectorStore

DIM = 64
CHUNK = Chunk(chunk_id="c-disk", source="runbook_disk_full.pdf", text="Disk full: run df -h.", heading_path=("Disk full", "Diagnosis"), page=2)


class DownEmbedder(Embedder):
    def __init__(self) -> None:
        super().__init__(dimension=DIM, batch_size=32, embedding_model="fake")

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        raise EmbeddingError("embedding server unreachable")


def rag_on(embedder: Embedder) -> RAG:
    store = InMemoryVectorStore(dimension=DIM, embedding_model="fake")
    store.upsert([CHUNK], FakeEmbedder(dimension=DIM).embed_batch([CHUNK.text]))
    return RAG(embedder=embedder, vector_store=store, chat_model=FakeChatModel(), top_k=10)


@pytest.fixture
def client_for() -> Iterator:
    """The agent's client, its requests served by the rag API with the given RAG (the lifespan's real adapters never run)."""

    def make(rag: RAG) -> HttpRagClient:
        app.dependency_overrides[get_rag] = lambda: rag
        return HttpRagClient("http://rag", timeout=10.0, retries=2, client=TestClient(app))

    yield make
    app.dependency_overrides.clear()


def test_a_search_returns_rags_hits(client_for) -> None:
    response = client_for(rag_on(FakeEmbedder(dimension=DIM))).search(SearchRequest(query="Disk full: run df -h.", top_k=1))

    [hit] = response.hits
    assert (hit.chunk_id, hit.source, hit.heading_path) == ("c-disk", "runbook_disk_full.pdf", ["Disk full", "Diagnosis"])


def test_a_failing_rag_backend_is_a_rag_error(client_for) -> None:
    # rag answers 502 when its embedder is down: the agent's window then ends in "error", not in a crash
    with pytest.raises(RagError, match="502"):
        client_for(rag_on(DownEmbedder())).search(SearchRequest(query="q"))
