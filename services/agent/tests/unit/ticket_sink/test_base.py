"""Contract tests: one suite written against `TicketSink`, run once per implementation (tooling/5).

The MCP sink runs against the stand-in server of conftest.py, whose dedup is the memory sink's: what this suite
checks is that both sinks give the agent the same answers, over the wire or not.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from siteops_agent.ticket_sink.base import TicketSink
from siteops_agent.ticket_sink.mcp import McpTicketSink
from siteops_agent.ticket_sink.memory import MemoryTicketSink
from siteops_common.contracts import TicketCreate

from .conftest import API_KEY, NOW

TICKET = TicketCreate(
    fault_type="disk_full",
    host="log-02",
    severity="SEV-2",
    title="/var/log full on log-02",
    summary="rsyslogd can't write: No space left on device.",
    suggested_fix="Free space under /var/log, then restart rsyslogd.",
    evidence=["2026-11-01T07:10:00.000Z ERROR log-02 rsyslogd: No space left on device (errno 28)"],
    window_start=datetime(2026, 11, 1, 7, 3, tzinfo=UTC),
    window_end=datetime(2026, 11, 1, 7, 13, tzinfo=UTC),
)

Advance = Callable[[timedelta], None]


@pytest.fixture(params=["memory", "mcp"])
def sink(request: pytest.FixtureRequest) -> tuple[TicketSink, Advance]:
    """A sink, and how to move its dedup clock."""
    if request.param == "memory":
        clock = [NOW]
        memory = MemoryTicketSink(now=lambda: clock[0])
        return memory, lambda delta: clock.__setitem__(0, clock[0] + delta)
    backend = request.getfixturevalue("backend")
    return McpTicketSink(request.getfixturevalue("mcp_url"), API_KEY, timeout=10.0, retries=2), backend.advance


def test_a_new_fault_gives_a_new_ticket(sink) -> None:
    tickets, _ = sink

    result = tickets.create(TICKET)

    assert result.created
    assert result.ticket.fingerprint == "disk_full|log-02"
    assert result.ticket.model_dump(include=set(TicketCreate.model_fields)) == TICKET.model_dump()  # filed as sent


def test_the_same_fault_within_the_window_returns_the_open_ticket(sink) -> None:
    tickets, advance = sink
    first = tickets.create(TICKET)

    advance(timedelta(minutes=59))
    again = tickets.create(TICKET.model_copy(update={"title": "still full", "host": " LOG-02 "}))  # same fingerprint

    assert not again.created and again.ticket.id == first.ticket.id


def test_another_host_or_fault_type_is_another_ticket(sink) -> None:
    tickets, _ = sink
    first = tickets.create(TICKET)

    other_host = tickets.create(TICKET.model_copy(update={"host": "log-01"}))
    other_fault = tickets.create(TICKET.model_copy(update={"fault_type": "other"}))

    assert other_host.created and other_fault.created
    assert len({first.ticket.id, other_host.ticket.id, other_fault.ticket.id}) == 3


def test_after_the_window_the_same_fault_is_a_new_ticket(sink) -> None:
    tickets, advance = sink
    first = tickets.create(TICKET)

    advance(timedelta(minutes=60))  # agent/3: created in the last 60 minutes, so exactly 60 is outside

    later = tickets.create(TICKET)

    assert later.created and later.ticket.id != first.ticket.id
