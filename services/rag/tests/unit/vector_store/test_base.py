"""Contract tests: one suite written against `VectorStore`, run once per implementation."""

import uuid
from collections.abc import Iterator

import pytest

from siteops_rag.embedder.base import Embeddings, Vector
from siteops_rag.models import Chunk
from siteops_rag.vector_store.base import VectorStore

DIM = 8
MODEL = "test-model"


@pytest.fixture(
    params=[
        "memory",
        pytest.param("qdrant", marks=pytest.mark.integration),  # needs the qdrant extra; in-process, no server
        pytest.param("qdrant-server", marks=pytest.mark.integration),  # a real server: filters and ids go over HTTP
    ]
)
def store(request: pytest.FixtureRequest) -> Iterator[VectorStore]:
    if request.param == "memory":
        from siteops_rag.vector_store.memory import InMemoryVectorStore

        yield InMemoryVectorStore(dimension=DIM, embedding_model=MODEL)
        return

    pytest.importorskip("qdrant_client")
    from qdrant_client import QdrantClient

    from siteops_rag.vector_store.qdrant import QdrantVectorStore

    client = QdrantClient(url=request.getfixturevalue("qdrant_url")) if request.param == "qdrant-server" else QdrantClient(":memory:")
    collection = f"test_{uuid.uuid4().hex}"  # fresh collection per test
    yield QdrantVectorStore(client=client, collection_name=collection, dimension=DIM, embedding_model=MODEL)
    client.delete_collection(collection)


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


def stored_ids(store: VectorStore) -> set[str]:
    return {hit.chunk.chunk_id for hit in store.search(vec(0), top_k=100)}


def test_upsert_deletes_stale_chunks_of_the_same_source(store: VectorStore) -> None:
    kept, edited, new, other = chunk("a1", "a.md"), chunk("a2", "a.md"), chunk("a3", "a.md"), chunk("b1", "b.md")
    store.upsert([kept, edited, other], emb(vec(0), vec(1), vec(2)))
    store.upsert([kept, new], emb(vec(0), vec(3)))
    assert stored_ids(store) == {kept.chunk_id, new.chunk_id, other.chunk_id}


def test_search_on_empty_store_returns_nothing(store: VectorStore) -> None:
    assert store.search(vec(0), top_k=5) == []


def test_top_k_larger_than_store_returns_everything(store: VectorStore) -> None:
    store.upsert([chunk("a"), chunk("b")], emb(vec(0), vec(1)))
    assert len(store.search(vec(0), top_k=10)) == 2


def test_upsert_rejects_another_embedding_model(store: VectorStore) -> None:
    with pytest.raises(ValueError):
        store.upsert([chunk("a")], Embeddings(vectors=[vec(0)], model="other"))


@pytest.mark.parametrize(
    "embeddings",
    [emb([1.0] * (DIM + 1)), emb(vec(0), vec(1)), emb()],
    ids=["wrong-dimension", "more-vectors-than-chunks", "fewer-vectors-than-chunks"],
)
def test_rejected_upsert_writes_nothing(store: VectorStore, embeddings: Embeddings) -> None:
    store.upsert([chunk("old")], emb(vec(0)))

    with pytest.raises(ValueError):
        store.upsert([chunk("a")], embeddings)
    # nothing written, and the source's existing chunks not cleaned up either
    assert stored_ids(store) == {chunk("old").chunk_id}


def test_empty_upsert_changes_nothing(store: VectorStore) -> None:
    store.upsert([chunk("a")], emb(vec(0)))

    store.upsert([], emb())

    assert store.count() == 1


def test_a_chunk_comes_back_exactly_as_stored(store: VectorStore) -> None:
    # Qdrant stores the payload as JSON: tuples come back as lists, ids as strings
    stored = Chunk(
        chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "full")),
        source="network/link_drop.md",
        text="Link drop > Fix\n\nReplace the cable: `ip link set eth0 up`.",
        heading_path=("Link drop", "Fix"),
        page=3,
    )
    store.upsert([stored], emb(vec(0)))

    [hit] = store.search(vec(0), top_k=1)

    assert hit.chunk == stored
