import json

import httpx
import pytest

from siteops_rag.llm.base import LLMError
from siteops_rag.llm.openai_compat import OpenAICompatChatModel


def make_chat_model(handler, **kwargs) -> OpenAICompatChatModel:
    """Chat model whose HTTP calls go to `handler` instead of the network."""
    return OpenAICompatChatModel(
        base_url="http://test/v1",
        model="m",
        temperature=0.2,
        api_key="k",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def test_sends_a_json_mode_chat_request_and_parses_the_reply() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion('{"answer": "Run df.", "citations": []}')

    reply = make_chat_model(handler).complete_json("the rules", "the question")

    assert reply == {"answer": "Run df.", "citations": []}
    [request] = requests
    assert str(request.url) == "http://test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer k"
    assert json.loads(request.content) == {
        "model": "m",
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": "the rules"}, {"role": "user", "content": "the question"}],
    }


def test_reasoning_effort_is_sent_only_when_configured() -> None:
    # "none" turns Ollama's thinking off; servers without thinking may reject an unknown field, so None omits it
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return completion("{}")

    make_chat_model(handler, reasoning_effort="none").complete_json("rules", "question")
    make_chat_model(handler).complete_json("rules", "question")

    assert bodies[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in bodies[1]


def _raise_connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


def _raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow model", request=request)


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(503),  # e.g. Ollama still loading the model
        lambda request: httpx.Response(404, json={"error": "model not found"}),  # model not pulled
        _raise_connect_error,  # server down
        _raise_timeout,  # answer slower than the timeout
        lambda request: httpx.Response(200, json={"oops": []}),  # body without "choices"
        lambda request: httpx.Response(200, json={"choices": []}),
        lambda request: completion("Sure! Run df -h."),  # prose instead of JSON
        lambda request: completion('["Run df -h"]'),  # JSON, but not an object
        lambda request: httpx.Response(200, text="<html>proxy error</html>"),  # body is not JSON at all
    ],
    ids=["http-503", "http-404", "connection-refused", "timeout", "no-choices", "empty-choices",
         "content-not-json", "content-not-an-object", "body-not-json"],
)
def test_backend_failures_raise_llm_error(handler) -> None:
    with pytest.raises(LLMError):
        make_chat_model(handler).complete_json("rules", "question")
