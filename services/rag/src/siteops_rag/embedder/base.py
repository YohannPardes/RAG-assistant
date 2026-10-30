import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence

logger = logging.getLogger(__name__)

Vector = list[float]


class EmbeddingError(Exception):
    """The embedding backend failed (unreachable, error response, malformed output)."""


class Embedder(ABC):
    """Turns texts into vectors.

    Subclasses implement only `_embed_batch` (one call to their backend).
    Everything every embedder must guarantee lives here, once:
    input validation, batching, and the "one vector per text, right size" check.
    """

    def __init__(self, dimension: int, batch_size: int = 32) -> None:
        if dimension <= 0 or batch_size <= 0:
            raise ValueError("dimension and batch_size must be positive")
        self.dimension = dimension
        self.batch_size = batch_size

    def embed(self, text: str) -> Vector:
        """Embed one text, e.g. a user query."""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[Vector]:
        """Embed many texts, returned in the same order. Splits into backend calls of `batch_size`."""
        if not texts:
            return []
        if any(not t.strip() for t in texts):
            raise ValueError("cannot embed empty or whitespace-only text")  # caller bug, not a backend failure

        vectors: list[Vector] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            result = self._embed_batch(batch)
            self._check(batch, result)
            vectors.extend(result)
        return vectors

    def _check(self, batch: list[str], result: list[Vector]) -> None:
        if len(result) != len(batch):
            raise EmbeddingError(f"expected {len(batch)} vectors, got {len(result)}")
        for vector in result:
            if len(vector) != self.dimension:
                # Usually a config mismatch: the model served is not the one configured.
                raise EmbeddingError(f"expected dimension {self.dimension}, got {len(vector)}")

    @abstractmethod
    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        """One backend call. `texts` is non-empty, has no blank strings, and len <= batch_size."""