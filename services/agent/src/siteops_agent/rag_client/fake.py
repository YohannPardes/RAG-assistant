from collections.abc import Sequence

from siteops_agent.rag_client.base import RagClient, RagError
from siteops_common.contracts import SearchHit, SearchRequest, SearchResponse


class FakeRagClient(RagClient):
    """Returns the same hits, best first, for every query (the fake, design/1). Records the requests it got."""

    def __init__(self, hits: Sequence[SearchHit] = (), error: RagError | None = None) -> None:
        self.hits = list(hits)
        self.error = error  # set: every search fails, as with an unreachable rag
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        top_k = request.top_k if request.top_k is not None else len(self.hits)
        return SearchResponse(hits=self.hits[:top_k], embedding_model="fake", latency_ms=0.0)
