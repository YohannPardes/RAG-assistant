from typing import Any

import pytest

from siteops_rag.models import Chunk
from siteops_rag.rag import I_DONT_KNOW, RAG, SYSTEM_PROMPT

DISK = Chunk(
    chunk_id="c-disk",
    source="runbook_disk_full.pdf",
    text="Disk full > Fix\n\nRun df -h /var/log to find what uses the space.",
    heading_path=("Disk full", "Fix"),
    page=2,
)
CERT = Chunk("c-cert", "runbook_cert_expiry.md", "Cert expiry\n\nRenew the certificate with cert-manager.", ("Cert expiry",), None)
LATENCY = Chunk("c-latency", "runbook_high_latency.txt", "High latency\n\nRestart the api pods one by one.", ("High latency",), None)

QUESTION = "disk full on /var/log, what uses the space"


def excerpt_number(rag: RAG, chunk: Chunk, top_k: int = 3) -> int:
    """The number the LLM sees for `chunk` in the prompt: its 1-based rank among the hits."""
    return [s.chunk.chunk_id for s in rag.search(QUESTION, top_k)].index(chunk.chunk_id) + 1


def cite(number: int, quote: str) -> dict[str, Any]:
    return {"excerpt": number, "quote": quote}


@pytest.fixture
def three_runbooks(add_chunks) -> None:
    add_chunks(DISK, CERT, LATENCY)


def test_nothing_retrieved_says_i_dont_know_without_calling_the_llm(rag, chat_model) -> None:
    response = rag.ask(QUESTION, top_k=3)

    assert response.answered is False
    assert response.answer == I_DONT_KNOW
    assert response.citations == [] and response.hits == []
    assert chat_model.calls == []


def test_cited_answer_is_returned_with_the_chunk_metadata(rag, chat_model, three_runbooks) -> None:
    chat_model.reply = {"answer": "Run df -h /var/log.", "citations": [cite(excerpt_number(rag, DISK), "Run df -h /var/log")]}

    response = rag.ask(QUESTION, top_k=3)

    assert response.answered is True
    assert response.answer == "Run df -h /var/log."
    [citation] = response.citations
    assert (citation.chunk_id, citation.source, citation.heading_path, citation.page) == ("c-disk", DISK.source, ["Disk full", "Fix"], 2)
    assert citation.quote == "Run df -h /var/log"


def test_quote_not_in_the_cited_chunk_gives_i_dont_know(rag, chat_model, three_runbooks) -> None:
    # the LLM invents a step the runbook does not contain
    chat_model.reply = {"answer": "Reboot the host.", "citations": [cite(excerpt_number(rag, DISK), "Reboot the host")]}

    response = rag.ask(QUESTION, top_k=3)

    assert response.answered is False
    assert response.answer == I_DONT_KNOW
    assert response.citations == []


def test_quote_from_another_excerpt_than_the_one_cited_is_rejected(rag, chat_model, three_runbooks) -> None:
    # the quote is real, but it belongs to the disk chunk, not the cert chunk the LLM points to
    chat_model.reply = {"answer": "Run df.", "citations": [cite(excerpt_number(rag, CERT), "Run df -h /var/log")]}

    assert rag.ask(QUESTION, top_k=3).answered is False


def test_quote_may_differ_in_whitespace_only(rag, chat_model, three_runbooks) -> None:
    chat_model.reply = {"answer": "Run df.", "citations": [cite(excerpt_number(rag, DISK), "Run  df -h\n/var/log")]}

    assert rag.ask(QUESTION, top_k=3).answered is True


@pytest.mark.parametrize(
    "reply",
    [
        {"answer": "x", "citations": [cite(0, "Run df")]},
        {"answer": "x", "citations": [cite(4, "Run df")]},  # only 3 excerpts were given
        {"answer": "x", "citations": [cite(-1, "Run df")]},
        {"answer": "x", "citations": [{"excerpt": "1", "quote": "Run df"}]},
        {"answer": "x", "citations": [{"excerpt": 1}]},
        {"answer": "x", "citations": [cite(1, "   ")]},
        {"answer": "x", "citations": ["[1] Run df"]},
        {"answer": "x", "citations": "1"},
        {"answer": "x"},
        {},
    ],
    ids=[
        "excerpt-0", "excerpt-past-the-end", "excerpt-negative", "excerpt-as-string", "no-quote",
        "blank-quote", "citation-not-an-object", "citations-not-a-list", "no-citations", "empty-reply",
    ],
)
def test_malformed_reply_gives_i_dont_know_instead_of_failing(rag, chat_model, three_runbooks, reply) -> None:
    chat_model.reply = reply

    response = rag.ask(QUESTION, top_k=3)

    assert response.answered is False
    assert response.answer == I_DONT_KNOW


def test_only_the_citations_that_check_out_are_kept(rag, chat_model, three_runbooks) -> None:
    number = excerpt_number(rag, DISK)
    chat_model.reply = {"answer": "Run df.", "citations": [cite(number, "Reboot the host"), cite(number, "Run df -h /var/log"), cite(9, "x")]}

    response = rag.ask(QUESTION, top_k=3)

    assert [c.quote for c in response.citations] == ["Run df -h /var/log"]


def test_hits_are_the_retrieved_chunks_best_first_even_without_an_answer(rag, three_runbooks) -> None:
    response = rag.ask(QUESTION, top_k=2)

    assert response.answered is False
    assert len(response.hits) == 2  # top_k reaches the store
    assert response.hits[0].chunk_id == "c-disk"  # the chunk sharing the question's words
    assert response.hits[0].score >= response.hits[1].score
    assert response.hits[0].text == DISK.text


def test_response_reports_models_and_latency(rag, three_runbooks) -> None:
    response = rag.ask(QUESTION, top_k=3)

    assert response.model == "fake"
    assert response.embedding_model == "fake"
    assert response.latency_ms >= 0


def test_llm_gets_the_rules_and_the_numbered_excerpts_in_rank_order(rag, chat_model, three_runbooks) -> None:
    rag.ask(QUESTION, top_k=3)

    [(system, user)] = chat_model.calls
    assert system == SYSTEM_PROMPT
    ranked = [s.chunk.text for s in rag.search(QUESTION, 3)]
    for number, text in enumerate(ranked, start=1):
        assert f"[{number}] {text}" in user
    assert user.endswith(f"Question: {QUESTION}")


def test_search_response_holds_the_hits_and_the_embedding_model(rag, three_runbooks) -> None:
    response = rag.search_response(QUESTION, top_k=1)

    assert [h.chunk_id for h in response.hits] == ["c-disk"]
    assert response.hits[0].heading_path == ["Disk full", "Fix"]
    assert response.embedding_model == "fake"
    assert response.latency_ms >= 0
