from siteops_rag.llm.fake import FakeChatModel


def test_default_reply_is_i_dont_know_without_citations() -> None:
    # a RAG built on the fake must behave like an LLM that found nothing, not crash
    assert FakeChatModel().complete_json("rules", "question") == {"answer": "I don't know.", "citations": []}


def test_returns_the_given_reply_and_records_every_call() -> None:
    model = FakeChatModel(reply={"answer": "Run df.", "citations": []})

    first = model.complete_json("rules", "q1")
    model.complete_json("rules", "q2")

    assert first == {"answer": "Run df.", "citations": []}
    assert model.calls == [("rules", "q1"), ("rules", "q2")]
    assert model.model == "fake"
