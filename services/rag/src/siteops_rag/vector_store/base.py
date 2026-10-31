from abc import ABC, abstractmethod

from pydantic.dataclasses import dataclass

from siteops_rag.embedder.base import Embeddings, Vector
from siteops_rag.models import Chunk


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk  # text, source, heading_path, page, chunk_id
    score: float  # cosine similarity to the query: higher = more similar


class VectorStore(ABC):
    """Subclasses implement `_upsert`; the checks every store must make live in `upsert`, once."""

    def __init__(self, dimension: int, embedding_model: str) -> None:
        self.dimension = dimension
        self.embedding_model = embedding_model

    @abstractmethod
    def search(self, query: Vector, top_k: int) -> list[ScoredChunk]:
        """Search for the top_k most similar vectors to the query vector."""

    def upsert(self, chunks: list[Chunk], embeddings: Embeddings) -> None:
        """Upsert the given chunks and their corresponding vectors into the store.

        The chunks are the complete set for their sources: a source's other chunks are deleted (rag/12).
        """
        if embeddings.model != self.embedding_model:
            raise ValueError(f"store holds {self.embedding_model!r} vectors, got {embeddings.model!r}")
        if len(chunks) != len(embeddings.vectors):
            raise ValueError(f"got {len(chunks)} chunks but {len(embeddings.vectors)} vectors")
        if any(len(v) != self.dimension for v in embeddings.vectors):
            raise ValueError(f"expected dimension {self.dimension}")
        if not chunks:
            return  # nothing to write, and no source to clean up
        self._upsert(chunks, embeddings.vectors)

    @abstractmethod
    def _upsert(self, chunks: list[Chunk], vectors: list[Vector]) -> None:
        """Write the points, then delete the stale ones. Inputs are checked and non-empty."""

    @abstractmethod
    def delete_by_source(self, source: str) -> int:
        """Delete all chunks and vectors associated with the given source.

        Returns the number of deleted chunks.
        """

    @abstractmethod
    def count(self) -> int:
        """Return the total number of chunks in the store."""
