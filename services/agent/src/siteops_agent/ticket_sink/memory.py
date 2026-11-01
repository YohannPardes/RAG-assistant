from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from siteops_agent.ticket_sink.base import TicketSink
from siteops_common.contracts import CreateTicketResult, Ticket, TicketCreate, fingerprint


class MemoryTicketSink(TicketSink):
    """Tickets kept in memory, deduplicated as the tickets service does (agent/3): the same fingerprint (from `common`,
    arch/4) and the same window. The fake for tests, and the sink of `--dry-run`: nothing is filed anywhere."""

    def __init__(self, dedup_window: timedelta = timedelta(minutes=60), now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.dedup_window = dedup_window
        self._now = now  # tests pass a fake clock
        self.tickets: list[Ticket] = []  # every ticket created, in order

    def create(self, ticket: TicketCreate) -> CreateTicketResult:
        key = fingerprint(ticket.fault_type, ticket.host)
        now = self._now()
        recent = [stored for stored in self.tickets if stored.fingerprint == key and stored.created_at > now - self.dedup_window]
        if recent:
            return CreateTicketResult(ticket=recent[-1], created=False)
        stored = Ticket.model_validate(ticket.model_dump() | {"id": f"T-{len(self.tickets) + 1}", "created_at": now, "fingerprint": key})
        self.tickets.append(stored)
        return CreateTicketResult(ticket=stored, created=True)
