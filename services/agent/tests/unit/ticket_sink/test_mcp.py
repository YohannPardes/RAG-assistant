import socket

import pytest

from siteops_agent.ticket_sink.base import TicketSinkError
from siteops_agent.ticket_sink.mcp import McpTicketSink

from .conftest import API_KEY
from .test_base import TICKET


def test_a_wrong_key_is_refused_and_not_retried(mcp_url, backend) -> None:
    with pytest.raises(TicketSinkError, match="API key"):
        McpTicketSink(mcp_url, "wrong-key-0123456789", timeout=10.0, retries=2).create(TICKET)

    assert backend.sink.tickets == []


def test_a_refused_ticket_is_an_error_with_the_services_reason(mcp_url, backend) -> None:
    backend.refuse = "the store is read-only"

    with pytest.raises(TicketSinkError, match="the store is read-only"):
        McpTicketSink(mcp_url, API_KEY, timeout=10.0, retries=2).create(TICKET)


def test_an_unreachable_service_is_an_error_after_the_retries() -> None:
    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with pytest.raises(TicketSinkError, match="unreachable after 2 attempts"):
        McpTicketSink(f"http://127.0.0.1:{port}/mcp", API_KEY, timeout=2, retries=1).create(TICKET)
