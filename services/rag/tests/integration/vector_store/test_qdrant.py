"""What a real Qdrant server adds to the contract suite (unit/vector_store/test_base.py runs that against it too)."""

import uuid
from collections.abc import Iterator

import pytest

from siteops_rag.embedder.base import Embeddings
from siteops_rag.models import Chunk

pytestmark = pytest.mark.integration
qdrant_client = pytest.importorskip("qdrant_client")

from siteops_rag.vector_store.qdrant import QdrantVectorStore  # noqa: E402  (needs the qdrant extra)

DIM = 8


@pytest.fixture
def collection(qdrant_url: str) -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex}"
    yield name
    qdrant_client.QdrantClient(url=qdrant_url).delete_collection(name)


def open_store(qdrant_url: str, collection: str, dimension: int = DIM) -> QdrantVectorStore:
    """A new client each time, like a service restart."""
    return QdrantVectorStore(qdrant_client.QdrantClient(url=qdrant_url), collection, dimension=dimension, embedding_model="m")


def chunk(name: str) -> Chunk:
    return Chunk(chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, name)), source="a.md", text=name, heading_path=("A",), page=None)


def test_chunks_survive_a_restart(qdrant_url, collection) -> None:
    # the ingestion job and the API are different processes: the API must see what the job wrote
    open_store(qdrant_url, collection).upsert([chunk("a"), chunk("b")], Embeddings(vectors=[[1.0] + [0.0] * 7, [0.0, 1.0] + [0.0] * 6], model="m"))

    reopened = open_store(qdrant_url, collection)

    assert reopened.count() == 2
    assert reopened.search([1.0] + [0.0] * 7, top_k=1)[0].chunk == chunk("a")


def test_collection_made_for_another_dimension_is_refused_at_startup(qdrant_url, collection) -> None:
    # e.g. the embedding model changed in config but the collection was not recreated
    open_store(qdrant_url, collection, dimension=DIM)

    with pytest.raises(ValueError, match="dimension"):
        open_store(qdrant_url, collection, dimension=DIM * 2)
