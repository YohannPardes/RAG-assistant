"""The agent's MCP ticket sink against the real tickets service: the wire contract, the key, the dedup (agent/3)."""

from datetime import UTC, datetime

import pytest

from siteops_agent.ticket_sink.base import TicketSinkError
from siteops_agent.ticket_sink.mcp import McpTicketSink
from siteops_common.contracts import TicketCreate

from .conftest import API_KEY

TICKET = TicketCreate(
    fault_type="pg_primary_down",
    host="db-primary-01",
    severity="SEV-1",
    title="PostgreSQL primary db-primary-01 unreachable",
    summary="The replica cannot reach its primary and is not promoted.",
    suggested_fix="Confirm the primary is down, then promote the replica.",
    evidence=["2026-11-01T09:31:31.000Z WARN db-replica-02 patroni: leader db-primary-01 unreachable; no automatic failover, cluster paused"],
    window_start=datetime(2026, 11, 1, 9, 31, 11, tzinfo=UTC),
    window_end=datetime(2026, 11, 1, 9, 45, 43, tzinfo=UTC),
)


def test_a_ticket_is_filed_then_deduplicated_by_the_service(tickets_url) -> None:
    sink = McpTicketSink(tickets_url, API_KEY)

    first = sink.create(TICKET)
    again = sink.create(TICKET.model_copy(update={"host": "DB-PRIMARY-01"}))  # same fingerprint (common's, arch/4)

    assert first.created and first.ticket.fingerprint == "pg_primary_down|db-primary-01"
    assert not again.created and again.ticket.id == first.ticket.id


def test_the_service_refuses_a_wrong_key(tickets_url) -> None:
    with pytest.raises(TicketSinkError):
        McpTicketSink(tickets_url, "wrong-key-0123456789").create(TICKET)
