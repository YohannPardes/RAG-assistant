import logging

import httpx

from siteops_rag.embedder.base import Embedder, EmbeddingError, Vector

logger = logging.getLogger(__name__)

class OpenAICompatEmbedder(Embedder):
    """Wraps an Embedder to provide an OpenAI-compatible interface."""

    def __init__(self, base_url: str, 
                 model_name: str, 
                 timeout: float = 30.0, 
                 batch_size: int = 32,
                 max_retries: int = 3,
                 transport: httpx.HTTPTransport | None = None,
                 api_key: str = "EMPTY"):
        
        self.base_url = base_url
        self.model_name = model_name
        self.timeout = timeout
        self.batch_size = batch_size

        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            # retries= only covers connection failures (server down, refused), not 5xx responses
            transport=transport or httpx.HTTPTransport(retries=max_retries),
        )

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        self._validate_input(texts)
        if len(texts) == 0:
            return []

    def close(self) -> None:
        self._client.close()

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        try:
            response = self._client.post("/embeddings", json={"model": self.model, "input": texts})
            response.raise_for_status()
            data = response.json()["data"]
            return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]
        except httpx.HTTPError as exc:  # connection, timeout, 4xx/5xx
            raise EmbeddingError(f"embedding request failed: {exc}") from exc
        except (KeyError, TypeError, ValueError) as exc:  # body is not the expected JSON shape
            raise EmbeddingError(f"malformed embedding response: {exc}") from exc