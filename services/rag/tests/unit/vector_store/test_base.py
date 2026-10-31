"""Contract tests: one suite written against `VectorStore`, run once per implementation."""

import uuid

import pytest

from siteops_rag.embedder.base import Embeddings, Vector
from siteops_rag.models import Chunk
from siteops_rag.vector_store.base import VectorStore

DIM = 8
MODEL = "test-model"


@pytest.fixture(
    params=[
        "memory",
        pytest.param("qdrant", marks=pytest.mark.integration),  # needs the qdrant extra
    ]
)
def store(request: pytest.FixtureRequest) -> VectorStore:
    if request.param == "memory":
        from siteops_rag.vector_store.memory import InMemoryVectorStore

        return InMemoryVectorStore(dimension=DIM, embedding_model=MODEL)

    pytest.importorskip("qdrant_client")
    from qdrant_client import QdrantClient

    from siteops_rag.vector_store.qdrant import QdrantVectorStore

    return QdrantVectorStore(
        client=QdrantClient(":memory:"),
        collection_name=f"test_{uuid.uuid4().hex}",  # fresh collection per test
        dimension=DIM,
        embedding_model=MODEL,
    )


def chunk(cid: str, source: str = "a.md", text: str = "text") -> Chunk:
    return Chunk(chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, cid)), source=source, text=text, heading_path=("Doc",), page=None)


def vec(i: int) -> Vector:
    """A unit vector pointing along axis i: easy to reason about similarity."""
    v = [0.0] * DIM
    v[i] = 1.0
    return v


def emb(*vectors: Vector) -> Embeddings:
    return Embeddings(vectors=list(vectors), model=MODEL)


def test_search_returns_the_matching_chunk_first(store: VectorStore) -> None:
    a, b = chunk("a"), chunk("b")
    store.upsert([a, b], emb(vec(0), vec(1)))
    hits = store.search(vec(0), top_k=2)
    assert hits[0].chunk == a
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].score > hits[1].score


def test_upsert_is_idempotent(store: VectorStore) -> None:
    store.upsert([chunk("a")], emb(vec(0)))
    store.upsert([chunk("a")], emb(vec(0)))
    assert store.count() == 1


def test_delete_by_source_removes_only_that_source(store: VectorStore) -> None:
    store.upsert([chunk("a1", "a.md"), chunk("a2", "a.md"), chunk("b1", "b.md")], emb(vec(0), vec(1), vec(2)))
    assert store.delete_by_source("a.md") == 2
    assert store.count() == 1
    assert store.delete_by_source("unknown.md") == 0


def test_search_on_empty_store_returns_nothing(store: VectorStore) -> None:
    assert store.search(vec(0), top_k=5) == []


def test_top_k_larger_than_store_returns_everything(store: VectorStore) -> None:
    store.upsert([chunk("a"), chunk("b")], emb(vec(0), vec(1)))
    assert len(store.search(vec(0), top_k=10)) == 2


def test_upsert_rejects_another_embedding_model(store: VectorStore) -> None:
    with pytest.raises(ValueError):
        store.upsert([chunk("a")], Embeddings(vectors=[vec(0)], model="other"))
