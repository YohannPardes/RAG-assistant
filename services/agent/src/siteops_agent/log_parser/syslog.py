import re

from siteops_agent.log_parser.base import LogParser
from siteops_agent.models import LogEvent

# "<timestamp> <LEVEL> <host> <component>: <message>", the format scripts/generate_logs.py writes.
# The component has no colon, so the first ": " after it ends it; the message keeps every later colon.
_LINE = re.compile(r"(\S+)\s+(\S+)\s+(\S+)\s+([^\s:]+):(?: (.*))?")


class SyslogStyleParser(LogParser):
    """Syslog-like lines: 2026-11-01T06:00:00.123Z ERROR log-02 rsyslogd: write error: No space left on device"""

    def _parse_line(self, line: str) -> LogEvent | None:
        match = _LINE.fullmatch(line)
        if match is None:
            return None
        timestamp, level_text, host, component, message = match.groups()

        time = self.parse_time(timestamp)
        level = self.parse_level(level_text)
        if time is None or level is None:
            return None

        message = message or ""
        return LogEvent(time, host, level, component, message, self.extract_fields(message), raw=line)
