import json

import httpx
import pytest

from siteops_rag.embedder.base import EmbeddingError
from siteops_rag.embedder.openai_compat import OpenAICompatEmbedder

DIM = 4

def make_embedder(handler, **kwargs) -> OpenAICompatEmbedder:
    """Embedder whose HTTP calls go to `handler` instead of the network."""
    return OpenAICompatEmbedder(
        base_url="http://test/v1", model_name="m", dimension=DIM, api_key="k",
        transport=httpx.MockTransport(handler), **kwargs,
    )


def test_sends_openai_request_and_returns_vectors_in_input_order():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        texts = json.loads(request.content)["input"]
        data = [{"index": i, "embedding": [float(i)] * DIM} for i in range(len(texts))]
        return httpx.Response(200, json={"data": data[::-1]})  # shuffled: the adapter must sort by index

    vectors = make_embedder(handler, batch_size=2).embed_batch(["a", "b", "c"])

    assert [v[0] for v in vectors] == [0.0, 1.0, 0.0]  # 2 batches: [a, b] then [c]
    assert [json.loads(r.content) for r in requests] == [
        {"model": "m", "input": ["a", "b"]},
        {"model": "m", "input": ["c"]},
    ]
    assert str(requests[0].url) == "http://test/v1/embeddings"
    assert requests[0].headers["Authorization"] == "Bearer k"


def _raise_connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(503),  # server error, e.g. vLLM still loading the model
        _raise_connect_error,  # server down
        lambda request: httpx.Response(200, json={"oops": []}),  # body without "data"
    ],
    ids=["http-503", "connection-refused", "malformed-body"],
)
def test_backend_failures_raise_embedding_error(handler):
    with pytest.raises(EmbeddingError):
        make_embedder(handler).embed("disk full")
