"""The API as it starts for real: the lifespan builds every adapter from config (env vars on top of config.yaml),
over the ingested sample runbooks, the local Ollama models and a real Qdrant."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from siteops_common.contracts import AskResponse, SearchResponse
from siteops_rag.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def client(ingested: Settings, monkeypatch) -> Iterator[TestClient]:
    # how docker-compose will point the service at its neighbours
    monkeypatch.setenv("RAG_EMBEDDING__BASE_URL", ingested.embedding.base_url)
    monkeypatch.setenv("RAG_LLM__BASE_URL", ingested.llm.base_url)
    monkeypatch.setenv("RAG_LLM__TIMEOUT", str(ingested.llm.timeout))
    monkeypatch.setenv("RAG_VECTOR_STORE__URL", ingested.vector_store.url)
    monkeypatch.setenv("RAG_VECTOR_STORE__COLLECTION", ingested.vector_store.collection)
    from siteops_rag.api import app

    with TestClient(app) as client:  # entering runs the lifespan
        yield client


def squash(text: str) -> str:
    return " ".join(text.split())


def test_search_finds_the_runbook(client) -> None:
    response = client.post("/search", json={"query": "The disk is full on /var/log", "top_k": 3})

    assert response.status_code == 200
    body = SearchResponse.model_validate(response.json())
    assert body.hits[0].source == "runbook_disk_full.pdf"
    assert body.embedding_model == "bge-m3"


def test_question_the_runbooks_answer_gets_a_cited_answer(client) -> None:
    response = client.post("/ask", json={"question": "The disk is full on /var/log. How do I find what uses the space?"})

    assert response.status_code == 200
    body = AskResponse.model_validate(response.json())
    assert body.answered is True, body.answer
    assert any(c.source == "runbook_disk_full.pdf" for c in body.citations)
    # every citation points to a retrieved chunk and quotes it: the check the eval will run
    hits = {h.chunk_id: h for h in body.hits}
    for citation in body.citations:
        assert squash(citation.quote) in squash(hits[citation.chunk_id].text)


def test_question_outside_the_runbooks_gets_i_dont_know(client) -> None:
    response = client.post("/ask", json={"question": "What is the capital of France?"})

    body = AskResponse.model_validate(response.json())
    assert body.answered is False, body.answer
    assert body.citations == []
    assert body.hits  # something was retrieved: the LLM, not the store, said no
