import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast

from siteops_agent.models import LEVELS, Level, LogEvent

# A whole word of the form key=value: "duration_ms=1168", "url=https://x/?a=b" (the value keeps its "=").
# Not "a = b" (spaces), not "x==" (an operator, not a value), not "x=" (no value).
_FIELD = re.compile(r"(?<!\S)([A-Za-z_][\w.]*)=([^\s=]\S*)")


class LogParser(ABC):
    """Text line -> LogEvent (agent/9).

    Subclasses implement only `_parse_line` (one line format).
    Everything every parser must guarantee lives here, once: bad input never raises,
    unparsable lines are counted, times are UTC-aware, `raw` is the line as read.
    """

    def parse_line(self, line: str) -> LogEvent | None:
        """One line, or None when it is malformed. Never raises on bad input."""
        line = line.rstrip("\r\n")
        try:
            return self._parse_line(line)
        except ValueError:
            return None

    def parse_lines(self, lines: Iterable[str]) -> tuple[list[LogEvent], int]:
        """(events in input order, number of unparsable lines). Blank lines are skipped, not counted."""
        events: list[LogEvent] = []
        unparsable = 0
        for line in lines:
            if not line.strip():
                continue
            event = self.parse_line(line)
            if event is None:
                unparsable += 1
            else:
                events.append(event)
        return events, unparsable

    @staticmethod
    def parse_time(text: str) -> datetime | None:
        """ISO 8601 with a zone ("Z" or "+02:00"), milliseconds optional, converted to UTC.
        None without a zone: guessing it would shift every `--since` comparison."""
        try:
            time = datetime.fromisoformat(text)
        except ValueError:
            return None
        return time.astimezone(UTC) if time.tzinfo is not None else None

    @staticmethod
    def parse_level(text: str) -> Level | None:
        """One of the five levels, exactly as written; anything else makes the line malformed."""
        return cast(Level, text) if text in LEVELS else None

    @staticmethod
    def extract_fields(message: str) -> dict[str, str]:
        """The key=value words of a message; when a key repeats, the first value wins."""
        fields: dict[str, str] = {}
        for key, value in _FIELD.findall(message):
            fields.setdefault(key, value)
        return fields

    @abstractmethod
    def _parse_line(self, line: str) -> LogEvent | None:
        """One line, without its line ending. None (or ValueError) when it does not match the format."""
