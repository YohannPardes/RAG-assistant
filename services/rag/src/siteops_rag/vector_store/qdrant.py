import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from qdrant_client import QdrantClient, models

from siteops_rag.embedder.base import Embeddings, Vector
from siteops_rag.ingestion.chunking import Chunk
from siteops_rag.vector_store.base import ScoredChunk, VectorStore

logger = logging.getLogger(__name__)


class ChunkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    source: str
    text: str
    heading_path: list[str]  # list, not tuple: it comes back from JSON as a list
    page: int | None
    embedding_model: str
    ingested_at: datetime


class QdrantVectorStore(VectorStore):
    """A vector store that uses Qdrant as the backend."""

    def __init__(self, client: QdrantClient, collection_name: str, distance_func: Literal["cosine", "euclidean"]) -> None:

        self.client = client

        distance_functions = {
            "cosine": models.Distance.COSINE,
            "euclidean": models.Distance.EUCLIDEAN,
        }

        self.distance_func = distance_functions[distance_func]
        self.collection_name = collection_name
        self.__create_collection(self.collection_name)

    def __create_collection(self, collection_name: str) -> None:

        if not self.client.collection_exists(collection_name):
            self.client.create_collection(
                collection_name,
                vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE),
            )
            logger.info(f"Collection '{collection_name}' created.")

        else:
            logger.info(f"Collection '{collection_name}' already exists. Skipping creation.")

    def upsert(self, chunks: list[Chunk], embeddings: Embeddings) -> None:
        """Insert or overwrite points in the collection."""

        assert len(chunks) == len(embeddings), "Chunks and embeddings must have the same length."

        now = datetime.now(UTC)
        points = [
            models.PointStruct(
                id=chunk.chunk_id,
                vector=vector,
                payload=ChunkPayload(
                    source=chunk.source,
                    text=chunk.text,
                    heading_path=list(chunk.heading_path),
                    page=chunk.page,
                    embedding_model=embeddings.embedding_model,
                    ingested_at=now,
                ).model_dump(mode="json"),
            )
            for chunk, vector in zip(chunks, embeddings.vectors, strict=True)
        ]
        self.client.upsert(self.collection_name, points=points)

    def delete_by_source(self, source: str) -> int:
        """Delete points from the collection by source."""
        source_filter = models.Filter(
            must=[
                models.FieldCondition(key="source", match=models.MatchValue(value=source)),
            ]
        )
        return self.client.delete(self.collection_name, points_selector=models.FilterSelector(filter=source_filter))

    def search(self, query: Vector, top_k: int) -> list[ScoredChunk]:
        """Search for the top-k most similar points in the collection."""
        hits = self.client.query_points(self.collection_name, query=query, limit=top_k, with_payload=True).points
        return [{"id": hit.id, "score": hit.score, "payload": hit.payload} for hit in hits]

    def count(self) -> int:
        """Count the number of points in the collection.
        Assuming there is a single collection
        """
        return self.client.count(self.collection_name).count
