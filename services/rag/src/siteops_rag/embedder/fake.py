import hashlib

from siteops_rag.embedder.base import Embedder, Vector


class FakeEmbedder(Embedder):
    """Deterministic, offline embedder for tests: texts sharing words get similar vectors."""

    def __init__(self, dimension: int = 64, batch_size: int = 32, embedding_model: str = "fake") -> None:
        super().__init__(dimension=dimension, batch_size=batch_size, embedding_model=embedding_model)
        self.calls: list[list[str]] = []  # what each backend call received, for assertions

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        self.calls.append(texts)
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> Vector:
        v = [0.0] * self.dimension
        for word in text.lower().split():
            bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimension
            v[bucket] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]
