from typing import Any

import pytest

from siteops_common.contracts import TicketCreate


@pytest.fixture
def ticket_payload() -> dict[str, Any]:
    """A valid create_ticket argument, as the agent sends it over the wire."""
    return {
        "fault_type": "disk_full",
        "host": "log-02",
        "severity": "SEV-2",
        "title": "/var/log full on log-02",
        "summary": "rsyslogd cannot write: /var/log reached 99%.",
        "suggested_fix": "Force a log rotation, then find what uses the space (RB-101, 5. Remediation).",
        "evidence": ["2026-11-01T07:09:40.120Z ERROR log-02 rsyslogd: ... No space left on device (errno 28)"],
        "runbook": {"chunk_id": "c1", "source": "runbook_disk_full.pdf", "heading_path": ["RB-101", "5. Remediation"]},
        "window_start": "2026-11-01T07:03:06Z",
        "window_end": "2026-11-01T07:13:09Z",
    }


@pytest.fixture
def ticket(ticket_payload) -> TicketCreate:
    return TicketCreate.model_validate(ticket_payload)
