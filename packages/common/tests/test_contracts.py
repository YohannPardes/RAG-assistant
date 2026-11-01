from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from siteops_common.contracts import (
    AskRequest,
    AskResponse,
    Citation,
    CreateTicketResult,
    SearchHit,
    SearchRequest,
    SearchResponse,
    Ticket,
    TicketCreate,
)

HIT = SearchHit(chunk_id="c1", source="runbook_disk_full.pdf", heading_path=["Disk full", "Fix"], page=2, text="Run df -h.", score=0.9)
START = datetime(2026, 11, 1, 7, 3, 6, tzinfo=UTC)
TICKET = {
    "fault_type": "disk_full",
    "host": "log-02",
    "severity": "SEV-2",
    "title": "/var/log full on log-02",
    "summary": "rsyslogd cannot write: /var/log reached 99%.",
    "suggested_fix": "Force a log rotation, then find what uses the space (RB-101, 5. Remediation).",
    "evidence": ["2026-11-01T07:09:40.120Z ERROR log-02 rsyslogd: ... No space left on device (errno 28)"],
    "runbook": {"chunk_id": "c1", "source": "runbook_disk_full.pdf", "heading_path": ["RB-101", "5. Remediation"]},
    "window_start": START.isoformat(),
    "window_end": (START + timedelta(minutes=10)).isoformat(),
}


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


def test_a_ticket_carries_a_suggested_fix() -> None:
    ticket = TicketCreate.model_validate(TICKET)

    assert ticket.suggested_fix.startswith("Force a log rotation")
    assert ticket.runbook is not None and ticket.runbook.source == "runbook_disk_full.pdf"


def test_a_ticket_without_a_runbook_is_allowed() -> None:
    # a real problem none of the runbooks covers: better no citation than an invented one
    ticket = TicketCreate.model_validate(TICKET | {"fault_type": "other", "runbook": None})

    assert ticket.runbook is None


@pytest.mark.parametrize(
    "change",
    [
        {"fault_type": "overheating"},
        {"severity": "SEV-0"},
        {"host": ""},
        {"title": "x" * 201},
        {"summary": ""},
        {"suggested_fix": ""},
        {"suggested_fix": "x" * 4001},
        {"evidence": []},
        {"evidence": [""]},
        {"evidence": ["line"] * 21},
        {"evidence": ["x" * 2001]},
        {"window_start": "2026-11-01T07:03:06"},  # no zone: comparisons with other times would be wrong
        {"window_end": (START - timedelta(seconds=1)).isoformat()},
        {"assignee": "me"},
    ],
    ids=[
        "unknown-fault-type",
        "unknown-severity",
        "empty-host",
        "long-title",
        "empty-summary",
        "empty-fix",
        "long-fix",
        "no-evidence",
        "empty-evidence-line",
        "too-many-evidence-lines",
        "long-evidence-line",
        "naive-time",
        "window-ends-before-it-starts",
        "unknown-field",
    ],
)
def test_invalid_tickets_are_rejected(change) -> None:
    with pytest.raises(ValidationError):
        TicketCreate.model_validate(TICKET | change)


def test_a_window_time_in_another_zone_is_the_same_instant() -> None:
    paris = START.astimezone(timezone(timedelta(hours=1)))

    ticket = TicketCreate.model_validate(TICKET | {"window_start": paris.isoformat()})

    assert ticket.window_start == START


def test_tickets_survive_a_trip_over_the_wire() -> None:
    ticket = Ticket.model_validate(TICKET | {"id": "T-1", "created_at": START.isoformat(), "fingerprint": "disk_full|log-02"})
    result = CreateTicketResult(ticket=ticket, created=False)

    assert CreateTicketResult.model_validate_json(result.model_dump_json()) == result
