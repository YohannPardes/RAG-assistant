from datetime import timedelta

from siteops_agent.drafts import DraftSigner
from siteops_agent.ticket_sink.base import TicketSinkError
from siteops_agent.ticket_sink.memory import MemoryTicketSink

from .conftest import AUTH, SECRET, TICKET


def test_a_signed_draft_is_filed_then_deduplicated(client, drafts, sink) -> None:
    token = drafts.sign(TICKET)

    first = client.post("/tickets", json={"draft_token": token}, headers=AUTH)
    again = client.post("/tickets", json={"draft_token": token}, headers=AUTH)

    assert first.status_code == 200 and first.json()["created"]
    assert first.json()["ticket"]["host"] == "db-primary-01" and first.json()["ticket"]["evidence"] == TICKET.evidence
    assert not again.json()["created"] and again.json()["ticket"]["id"] == first.json()["ticket"]["id"]  # "already open as T-1"
    assert len(sink.tickets) == 1


def test_an_altered_draft_is_refused_and_nothing_is_filed(client, drafts, sink) -> None:
    # the browser could edit the evidence or the window times: the signature no longer matches
    payload, signature = drafts.sign(TICKET).split(".")
    forged = DraftSigner(b"x" * 32, ttl=timedelta(minutes=30)).sign(TICKET.model_copy(update={"host": "api-01"})).split(".")[0]

    for token in (f"{forged}.{signature}", f"{payload}.{signature[:-2]}AA", "not-a-token"):
        response = client.post("/tickets", json={"draft_token": token}, headers=AUTH)
        assert response.status_code == 422 and response.json()["code"] == "invalid_draft"
    assert sink.tickets == []


def test_an_expired_draft_is_refused(client, sink) -> None:
    expired = DraftSigner(SECRET, ttl=timedelta(seconds=-1)).sign(TICKET)

    response = client.post("/tickets", json={"draft_token": expired}, headers=AUTH)

    assert response.status_code == 422 and "expired" in response.json()["detail"]
    assert sink.tickets == []


def test_without_a_tickets_service_confirming_is_disabled(make_client, drafts) -> None:
    response = make_client(None).post("/tickets", json={"draft_token": drafts.sign(TICKET)}, headers=AUTH)

    assert response.status_code == 503 and response.json()["code"] == "tickets_disabled"


def test_a_failing_tickets_service_is_a_502(make_client, drafts) -> None:
    class DownSink(MemoryTicketSink):
        def create(self, ticket):  # type: ignore[no-untyped-def]
            raise TicketSinkError("tickets service unreachable after 3 attempts")

    response = make_client(DownSink()).post("/tickets", json={"draft_token": drafts.sign(TICKET)}, headers=AUTH)

    assert response.status_code == 502 and response.json()["code"] == "upstream_failed"
