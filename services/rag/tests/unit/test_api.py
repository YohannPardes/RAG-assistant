import pytest

from siteops_common.contracts import AskResponse, SearchResponse
from siteops_rag.llm.base import LLMError
from siteops_rag.llm.fake import FakeChatModel
from siteops_rag.models import Chunk
from siteops_rag.rag import RAG

DISK = Chunk("c-disk", "runbook_disk_full.pdf", "Disk full\n\nRun df -h /var/log to find what uses the space.", ("Disk full",), 2)


class _FailingChatModel(FakeChatModel):
    def complete_json(self, system: str, user: str) -> dict[str, object]:
        raise LLMError("chat server unreachable")


def test_health_answers_ok(api_client) -> None:
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ask_returns_a_cited_answer(api_client, chat_model, add_chunks) -> None:
    add_chunks(DISK)
    chat_model.reply = {"answer": "Run df -h /var/log.", "citations": [{"excerpt": 1, "quote": "Run df -h /var/log"}]}

    response = api_client.post("/ask", json={"question": "disk full on /var/log", "top_k": 1})

    assert response.status_code == 200
    body = AskResponse.model_validate(response.json())  # the agent and the eval parse it the same way
    assert body.answered is True
    assert [c.chunk_id for c in body.citations] == ["c-disk"]
    assert [h.chunk_id for h in body.hits] == ["c-disk"]


def test_ask_default_top_k_is_five(api_client, add_chunks) -> None:
    add_chunks(*(Chunk(f"c{i}", "a.md", f"disk step {i}", ("A",), None) for i in range(7)))

    response = api_client.post("/ask", json={"question": "disk"})

    assert len(response.json()["hits"]) == 5


def test_search_returns_the_ranked_hits(api_client, add_chunks) -> None:
    add_chunks(DISK, Chunk("c-cert", "runbook_cert_expiry.md", "Renew the certificate.", ("Cert",), None))

    response = api_client.post("/search", json={"query": "disk full /var/log", "top_k": 2})

    assert response.status_code == 200
    body = SearchResponse.model_validate(response.json())
    assert [h.chunk_id for h in body.hits] == ["c-disk", "c-cert"]
    assert body.embedding_model == "fake"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/ask", {}),
        ("/ask", {"question": ""}),
        ("/ask", {"question": "x", "top_k": 0}),
        ("/ask", {"question": "x", "top_k": 51}),
        ("/ask", {"question": "x", "temperature": 1}),
        ("/search", {"query": ""}),
        ("/search", {"query": "x", "top_k": 0}),
        ("/search", {"question": "x"}),  # the /ask field name sent to /search
    ],
    ids=["ask-missing-question", "ask-empty-question", "ask-top-k-0", "ask-top-k-51", "ask-unknown-field",
         "search-empty-query", "search-top-k-0", "search-wrong-field"],
)
def test_invalid_requests_are_rejected_with_422(api_client, path, payload) -> None:
    assert api_client.post(path, json=payload).status_code == 422


@pytest.mark.parametrize(("path", "payload"), [("/ask", {"question": "disk full"}), ("/search", {"query": "disk full"})], ids=["ask", "search"])
def test_embedding_backend_failure_is_a_502(client_for, failing_embedder, store, chat_model, path, payload) -> None:
    client = client_for(RAG(embedder=failing_embedder, vector_store=store, chat_model=chat_model))

    response = client.post(path, json=payload)

    assert response.status_code == 502
    assert "embedding server unreachable" in response.json()["detail"]


def test_llm_backend_failure_is_a_502(client_for, embedder, store, add_chunks) -> None:
    add_chunks(DISK)  # something must be retrieved, or the LLM is never called
    client = client_for(RAG(embedder=embedder, vector_store=store, chat_model=_FailingChatModel()))

    response = client.post("/ask", json={"question": "disk full"})

    assert response.status_code == 502
    assert "chat server unreachable" in response.json()["detail"]


def test_lifespan_builds_the_rag_the_routes_use(monkeypatch, rag) -> None:
    from fastapi.testclient import TestClient

    import siteops_rag.api as api

    monkeypatch.setattr(api, "build_rag", lambda: rag)  # stands in for the real adapters
    with TestClient(api.app) as client:  # entering runs the lifespan
        response = client.post("/ask", json={"question": "disk full"})

    assert response.status_code == 200
    assert response.json()["answered"] is False  # empty store: "I don't know"
