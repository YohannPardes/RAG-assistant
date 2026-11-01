from collections.abc import Iterable
from datetime import datetime

from siteops_agent.log_source.base import LogBatch, LogSource
from siteops_agent.models import LogEvent


class MemoryLogSource(LogSource):
    """Events held in memory (the fake, design/1): tests build them directly, without a file or a parser."""

    def __init__(self, events: Iterable[LogEvent], unparsable: int = 0) -> None:
        self._batch = LogBatch(tuple(events), unparsable)

    def _read(self, since: datetime, until: datetime) -> LogBatch:
        return self._batch
