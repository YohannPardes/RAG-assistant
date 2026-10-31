import logging
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from qdrant_client import QdrantClient, models

from siteops_rag.embedder.base import Vector
from siteops_rag.models import Chunk
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

    def __init__(self, client: QdrantClient, collection_name: str, dimension: int, embedding_model: str) -> None:
        super().__init__(dimension=dimension, embedding_model=embedding_model)

        self.client = client

        self.distance_func = models.Distance.COSINE
        self.collection_name = collection_name
        self._create_collection(self.collection_name)

    def _create_collection(self, collection_name: str) -> None:

        if not self.client.collection_exists(collection_name):
            self.client.create_collection(
                collection_name,
                vectors_config=models.VectorParams(size=self.dimension, distance=self.distance_func),
            )
            logger.info("Collection '%s' created.", collection_name)

        else:
            # an existing collection must match the configured model, or every upsert fails later
            params = self.client.get_collection(collection_name).config.params.vectors
            if not isinstance(params, models.VectorParams) or params.size != self.dimension:
                raise ValueError(f"collection '{collection_name}' does not hold {self.dimension}-dimension vectors")
            logger.info("Collection '%s' already exists. Skipping creation.", collection_name)

    def _upsert(self, chunks: list[Chunk], vectors: list[Vector]) -> None:
        """Insert or overwrite points in the collection."""
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
                    embedding_model=self.embedding_model,
                    ingested_at=now,
                ).model_dump(mode="json"),
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.client.upsert(self.collection_name, points=points)
        self._delete_stale(chunks)

    def _delete_stale(self, chunks: list[Chunk]) -> None:
        """Delete the chunks of the given chunks' sources that are not among them (rag/12)."""
        stale_filter = models.Filter(
            must=[models.FieldCondition(key="source", match=models.MatchAny(any=list({chunk.source for chunk in chunks})))],
            must_not=[models.HasIdCondition(has_id=[chunk.chunk_id for chunk in chunks])],
        )
        self.client.delete(self.collection_name, points_selector=models.FilterSelector(filter=stale_filter))

    def delete_by_source(self, source: str) -> int:
        """Delete points from the collection by source."""
        source_filter = models.Filter(
            must=[
                models.FieldCondition(key="source", match=models.MatchValue(value=source)),
            ]
        )
        # delete does not say how many points it removed: count them first
        deleted = self.client.count(self.collection_name, count_filter=source_filter, exact=True).count
        self.client.delete(self.collection_name, points_selector=models.FilterSelector(filter=source_filter))
        return deleted

    def search(self, query: Vector, top_k: int) -> list[ScoredChunk]:
        """Search for the top-k most similar points in the collection."""
        hits = self.client.query_points(self.collection_name, query=query, limit=top_k, with_payload=True).points
        return [ScoredChunk(chunk=self._to_chunk(hit), score=hit.score) for hit in hits]

    @staticmethod
    def _to_chunk(hit: models.ScoredPoint) -> Chunk:
        payload = ChunkPayload.model_validate(hit.payload)
        return Chunk(
            chunk_id=str(hit.id),
            source=payload.source,
            text=payload.text,
            heading_path=tuple(payload.heading_path),
            page=payload.page,
        )

    def count(self) -> int:
        """Count the number of points in the collection.
        Assuming there is a single collection
        """
        return self.client.count(self.collection_name).count
