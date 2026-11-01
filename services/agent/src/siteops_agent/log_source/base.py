from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from siteops_agent.models import LogEvent


@dataclass(frozen=True)
class LogBatch:
    """What one read gives: the events, and how many records could not be turned into one."""

    events: tuple[LogEvent, ...]
    unparsable: int = 0  # goes in the triage report: a jump in it is itself a signal (agent/9)


class LogSource(ABC):
    """Where the logs come from (agent/13): a file today; a database, an HTTP endpoint or a stream later.

    Every source gives LogEvents, so nothing downstream knows where they came from.
    A text source parses its lines with a LogParser; a structured one (database rows) builds the events itself.
    Subclasses implement only `_read`. The guarantees every source shares live here, once:
    only events in [since, until), in time order.
    """

    def read(self, since: datetime, until: datetime) -> LogBatch:
        """The events with since <= time < until, in time order (ties keep the source's order).

        A closed range, not "until now": the same range read twice gives the same events, which is what makes
        a triage run repeatable (agent/4).
        """
        if since.tzinfo is None or until.tzinfo is None:
            raise ValueError("since and until must be timezone-aware: event times are UTC")
        if since > until:
            raise ValueError(f"since ({since}) is after until ({until})")
        batch = self._read(since, until)
        events = sorted((event for event in batch.events if since <= event.time < until), key=lambda event: event.time)
        return LogBatch(tuple(events), batch.unparsable)

    @abstractmethod
    def _read(self, since: datetime, until: datetime) -> LogBatch:
        """The events of the range, in any order. It may return events outside the range (read() drops them), so a source
        that can't filter by time doesn't have to; one that can (a database query) should, to read less.
        `unparsable` counts the records read that gave no event, whatever their time: an unparsable line has none."""
