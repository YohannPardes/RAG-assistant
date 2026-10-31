import logging

import httpx

from siteops_rag.embedder.base import Embedder, EmbeddingError, Vector

logger = logging.getLogger(__name__)


class OpenAICompatEmbedder(Embedder):
    """Calls an OpenAI-compatible `/embeddings` endpoint (TEI, vLLM, Ollama...)."""

    def __init__(
        self,
        base_url: str,
        embedding_model: str,
        timeout: float = 30.0,
        batch_size: int = 32,
        dimension: int = 1024,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        api_key: str = "EMPTY",
    ):

        super().__init__(dimension=dimension, batch_size=batch_size, embedding_model=embedding_model)

        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            # retries= only covers connection failures (server down, refused), not 5xx responses
            transport=transport or httpx.HTTPTransport(retries=max_retries),
        )

    def close(self) -> None:
        self._client.close()

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        try:
            response = self._client.post("/embeddings", json={"model": self.embedding_model, "input": texts})
            response.raise_for_status()
            data = response.json()["data"]
            return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]
        except httpx.HTTPError as exc:  # connection, timeout, 4xx/5xx
            raise EmbeddingError(f"embedding request failed: {exc}") from exc
        except (KeyError, TypeError, ValueError) as exc:  # body is not the expected JSON shape
            raise EmbeddingError(f"malformed embedding response: {exc}") from exc
