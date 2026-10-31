import math

from siteops_rag.embedder.base import Vector
from siteops_rag.models import Chunk
from siteops_rag.vector_store.base import ScoredChunk, VectorStore


def _cosine(a: Vector, b: Vector) -> float:
    return math.sumprod(a, b) / ((math.hypot(*a) * math.hypot(*b)) or 1.0)


class InMemoryVectorStore(VectorStore):
    """Dict-backed store for tests: brute-force cosine search, nothing persisted."""

    def __init__(self, dimension: int, embedding_model: str) -> None:
        super().__init__(dimension=dimension, embedding_model=embedding_model)
        self._points: dict[str, tuple[Chunk, Vector]] = {}  # chunk_id -> (chunk, vector)

    def _upsert(self, chunks: list[Chunk], vectors: list[Vector]) -> None:
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._points[chunk.chunk_id] = (chunk, vector)
        self._delete_stale(chunks)

    def _delete_stale(self, chunks: list[Chunk]) -> None:
        """Delete the chunks of the given chunks' sources that are not among them (rag/12)."""
        sources = {chunk.source for chunk in chunks}
        keep = {chunk.chunk_id for chunk in chunks}
        stale = [cid for cid, (chunk, _) in self._points.items() if chunk.source in sources and cid not in keep]
        for cid in stale:
            del self._points[cid]

    def search(self, query: Vector, top_k: int) -> list[ScoredChunk]:
        scored = [ScoredChunk(chunk=chunk, score=_cosine(query, vector)) for chunk, vector in self._points.values()]
        return sorted(scored, key=lambda s: s.score, reverse=True)[:top_k]

    def delete_by_source(self, source: str) -> int:
        ids = [cid for cid, (chunk, _) in self._points.items() if chunk.source == source]
        for cid in ids:
            del self._points[cid]
        return len(ids)

    def count(self) -> int:
        return len(self._points)
