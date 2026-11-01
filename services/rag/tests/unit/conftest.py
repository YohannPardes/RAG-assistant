"""Fixtures shared by the unit tests: the fake adapters, a RAG built on them, and API clients."""

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from siteops_rag.embedder.base import Embedder, EmbeddingError, Vector
from siteops_rag.embedder.fake import FakeEmbedder
from siteops_rag.llm.fake import FakeChatModel
from siteops_rag.models import Chunk
from siteops_rag.rag import RAG
from siteops_rag.vector_store.memory import InMemoryVectorStore

DIM = 64


class _FailingEmbedder(Embedder):
    """An embedding backend that is down."""

    def __init__(self) -> None:
        super().__init__(dimension=DIM, batch_size=32, embedding_model="fake")

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        raise EmbeddingError("embedding server unreachable")


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder(dimension=DIM)


@pytest.fixture
def failing_embedder() -> Embedder:
    return _FailingEmbedder()


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore(dimension=DIM, embedding_model="fake")


@pytest.fixture
def chat_model() -> FakeChatModel:
    """Says "I don't know" until a test sets `chat_model.reply`."""
    return FakeChatModel()


@pytest.fixture
def rag(embedder: FakeEmbedder, store: InMemoryVectorStore, chat_model: FakeChatModel) -> RAG:
    return RAG(embedder=embedder, vector_store=store, chat_model=chat_model, top_k=5)


@pytest.fixture
def add_chunks(embedder: FakeEmbedder, store: InMemoryVectorStore) -> Callable[..., None]:
    """Store chunks the way ingestion does: embedded with the same embedder the RAG queries with."""

    def add(*chunks: Chunk) -> None:
        store.upsert(list(chunks), embedder.embed_batch([c.text for c in chunks]))

    return add


@pytest.fixture
def client_for() -> Iterator[Callable[[RAG], TestClient]]:
    """API client whose routes get the given RAG. Not entered as a context manager, so the
    lifespan (which builds the real adapters) never runs."""
    from siteops_rag.api import app, get_rag

    def make(rag: RAG) -> TestClient:
        app.dependency_overrides[get_rag] = lambda: rag
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def api_client(client_for: Callable[[RAG], TestClient], rag: RAG) -> TestClient:
    return client_for(rag)
