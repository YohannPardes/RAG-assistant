from abc import ABC, abstractmethod

from pydantic.dataclasses import dataclass

from siteops_rag.embedder.base import Embeddings, Vector
from siteops_rag.models import Chunk


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk  # text, source, heading_path, page, chunk_id
    score: float  # cosine similarity to the query: higher = more similar


class VectorStore(ABC):
    @abstractmethod
    def search(self, query: Vector, top_k: int) -> list[ScoredChunk]:
        """Search for the top_k most similar vectors to the query vector."""

    @abstractmethod
    def upsert(self, chunks: list[Chunk], embeddings: Embeddings) -> None:
        """Upsert the given chunks and their corresponding vectors into the store."""

    @abstractmethod
    def delete_by_source(self, source: str) -> int:
        """Delete all chunks and vectors associated with the given source.

        Returns the number of deleted chunks.
        """

    @abstractmethod
    def count(self) -> int:
        """Return the total number of chunks in the store."""
