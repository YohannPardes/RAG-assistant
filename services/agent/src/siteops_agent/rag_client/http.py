import httpx
from pydantic import ValidationError

from siteops_agent.rag_client.base import RagClient, RagError
from siteops_common.contracts import SearchRequest, SearchResponse


class HttpRagClient(RagClient):
    """rag's POST /search over HTTP. Connection errors are retried (arch/7): /search is read-only, so it is safe."""

    def __init__(self, base_url: str, timeout: float, retries: int, client: httpx.Client | None = None) -> None:
        # `client`: tests pass one bound to an in-process app (Starlette's TestClient) or to a mock transport
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)
        self.retries = retries

    def search(self, request: SearchRequest) -> SearchResponse:
        response = self._post(request)
        if response.status_code != 200:
            raise RagError(f"rag /search answered {response.status_code}: {response.text[:200]}")
        try:
            return SearchResponse.model_validate_json(response.content)
        except ValidationError as error:
            raise RagError(f"rag /search sent an invalid reply: {error}") from error

    def close(self) -> None:
        self._client.close()

    def _post(self, request: SearchRequest) -> httpx.Response:
        for attempt in range(self.retries + 1):
            try:
                return self._client.post("/search", json=request.model_dump(mode="json"))
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:  # the request never reached rag
                if attempt == self.retries:
                    raise RagError(f"rag unreachable after {attempt + 1} attempts: {error!r}") from error
            except httpx.HTTPError as error:  # it may have reached rag: not retried
                raise RagError(f"rag /search failed: {error!r}") from error
        raise AssertionError("unreachable")  # the loop returns or raises
