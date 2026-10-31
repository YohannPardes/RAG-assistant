import pytest
from pydantic import ValidationError

from siteops_common.contracts import AskRequest, AskResponse, Citation, SearchHit, SearchRequest, SearchResponse

HIT = SearchHit(chunk_id="c1", source="runbook_disk_full.pdf", heading_path=["Disk full", "Fix"], page=2, text="Run df -h.", score=0.9)


@pytest.mark.parametrize("model", [SearchRequest, AskRequest])
def test_requests_default_to_five_chunks(model) -> None:
    field = "query" if model is SearchRequest else "question"
    assert model(**{field: "disk full"}).top_k == 5


@pytest.mark.parametrize(
    "payload",
    [{"query": ""}, {"query": "x", "top_k": 0}, {"query": "x", "top_k": 51}, {"query": "x", "k": 3}, {}],
    ids=["empty-query", "top-k-0", "top-k-51", "unknown-field", "missing-query"],
)
def test_invalid_search_requests_are_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(payload)


def test_a_misspelled_response_field_is_rejected_not_ignored() -> None:
    # the agent parses rag's responses: a renamed field must fail loudly, not come back as a default
    payload = HIT.model_dump() | {"scor": 0.9}

    with pytest.raises(ValidationError):
        SearchHit.model_validate(payload)


def test_messages_cannot_be_changed_once_received() -> None:
    with pytest.raises(ValidationError):
        HIT.score = 1.0


def test_responses_survive_a_trip_over_the_wire() -> None:
    citation = Citation(chunk_id="c1", source=HIT.source, heading_path=HIT.heading_path, page=None, quote="Run df -h.")
    responses = [
        SearchResponse(hits=[HIT], embedding_model="bge-m3", latency_ms=12.5),
        AskResponse(
            answered=True, answer="Run df -h.", citations=[citation], hits=[HIT], model="qwen3.5:27b", embedding_model="bge-m3", latency_ms=900.0
        ),
    ]

    for response in responses:
        assert type(response).model_validate_json(response.model_dump_json()) == response
