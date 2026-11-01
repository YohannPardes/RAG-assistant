import json
from collections.abc import Callable

import httpx
import pytest

from siteops_agent.rag_client.base import RagError
from siteops_agent.rag_client.http import HttpRagClient
from siteops_common.contracts import SearchHit, SearchRequest, SearchResponse

HIT = SearchHit(chunk_id="c1", source="runbook_disk_full.pdf", heading_path=["Disk full"], page=2, text="Run df -h.", score=0.9)
REPLY = SearchResponse(hits=[HIT], embedding_model="bge-m3", latency_ms=12.0)

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler, retries: int = 2) -> tuple[HttpRagClient, list[httpx.Request]]:
    """A client whose requests go to `handler`, and the requests it made."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.Client(base_url="http://rag", transport=httpx.MockTransport(record))
    return HttpRagClient("http://rag", timeout=10.0, retries=retries, client=transport), seen


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=REPLY.model_dump_json())


def failing_first(times: int, error: type[httpx.TransportError]) -> Handler:
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        if calls[0] <= times:
            raise error("down", request=request)
        return ok(request)

    return handler


def test_a_search_posts_the_request_and_returns_the_reply() -> None:
    rag, seen = client(ok)

    assert rag.search(SearchRequest(query="disk full", top_k=3)) == REPLY
    [request] = seen
    assert (request.method, request.url.path) == ("POST", "/search")
    assert json.loads(request.content) == {"query": "disk full", "top_k": 3}


def test_no_top_k_is_sent_as_null_so_rag_uses_its_default() -> None:
    # rag/7: the default lives in rag's config only
    rag, seen = client(ok)

    rag.search(SearchRequest(query="disk full"))

    assert json.loads(seen[0].content)["top_k"] is None


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ConnectTimeout])
def test_connection_errors_are_retried(error) -> None:
    rag, seen = client(failing_first(2, error), retries=2)

    assert rag.search(SearchRequest(query="q")) == REPLY
    assert len(seen) == 3


def test_after_the_retries_a_connection_error_is_a_rag_error() -> None:
    rag, seen = client(failing_first(3, httpx.ConnectError), retries=2)

    with pytest.raises(RagError, match="unreachable after 3 attempts"):
        rag.search(SearchRequest(query="q"))
    assert len(seen) == 3


def test_a_request_that_may_have_reached_rag_is_not_retried() -> None:
    rag, seen = client(failing_first(1, httpx.ReadTimeout))

    with pytest.raises(RagError, match="failed"):
        rag.search(SearchRequest(query="q"))
    assert len(seen) == 1


@pytest.mark.parametrize(
    "response",
    [httpx.Response(502, text="upstream failed"), httpx.Response(200, json={"hits": "not a list"}), httpx.Response(200, text="not json")],
    ids=["error-status", "wrong-shape", "not-json"],
)
def test_a_bad_reply_is_a_rag_error_and_not_retried(response) -> None:
    rag, seen = client(lambda request: response)

    with pytest.raises(RagError):
        rag.search(SearchRequest(query="q"))
    assert len(seen) == 1
