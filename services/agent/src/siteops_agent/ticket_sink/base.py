from abc import ABC, abstractmethod

from siteops_common.contracts import CreateTicketResult, TicketCreate


class TicketSinkError(Exception):
    """The ticket could not be filed: the tickets service is unreachable, refused the key, or refused the ticket."""


class TicketSink(ABC):
    """Where the agent files tickets: its only side effect."""

    @abstractmethod
    def create(self, ticket: TicketCreate) -> CreateTicketResult:
        """The new ticket (created=True), or the recent one with the same fingerprint (created=False: deduplicated).
        Raises TicketSinkError when the ticket could not be filed."""
