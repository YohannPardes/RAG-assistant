import math

from siteops_rag.embedder.base import Embeddings, Vector
from siteops_rag.models import Chunk
from siteops_rag.vector_store.base import ScoredChunk, VectorStore


def _cosine(a: Vector, b: Vector) -> float:
    return math.sumprod(a, b) / ((math.hypot(*a) * math.hypot(*b)) or 1.0)


class InMemoryVectorStore(VectorStore):
    """Dict-backed store for tests: brute-force cosine search, nothing persisted."""

    def __init__(self, dimension: int, embedding_model: str) -> None:
        self.dimension = dimension
        self.embedding_model = embedding_model
        self._points: dict[str, tuple[Chunk, Vector]] = {}  # chunk_id -> (chunk, vector)

    def upsert(self, chunks: list[Chunk], embeddings: Embeddings) -> None:
        if embeddings.model != self.embedding_model:
            raise ValueError(f"store holds {self.embedding_model!r} vectors, got {embeddings.model!r}")
        if any(len(v) != self.dimension for v in embeddings.vectors):
            raise ValueError(f"expected dimension {self.dimension}")
        for chunk, vector in zip(chunks, embeddings.vectors, strict=True):
            self._points[chunk.chunk_id] = (chunk, vector)

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
