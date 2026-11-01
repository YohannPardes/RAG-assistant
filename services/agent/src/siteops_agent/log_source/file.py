from datetime import datetime
from pathlib import Path

from siteops_agent.log_parser.base import LogParser
from siteops_agent.log_parser.syslog import SyslogStyleParser
from siteops_agent.log_source.base import LogBatch, LogSource


class FileLogSource(LogSource):
    """A log file, one event per line, in the parser's format."""

    def __init__(self, path: Path, parser: LogParser | None = None) -> None:
        self.path = path
        self._parser = parser or SyslogStyleParser()

    def _read(self, since: datetime, until: datetime) -> LogBatch:
        # a missing file raises: it is a configuration error, not an empty log.
        # Undecodable bytes are replaced, not fatal: the line is still read, and parses or is counted.
        with self.path.open(encoding="utf-8", errors="replace") as lines:
            events, unparsable = self._parser.parse_lines(lines)
        return LogBatch(tuple(events), unparsable)  # read() keeps the range: a line's time is only known once parsed
