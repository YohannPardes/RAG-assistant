from datetime import UTC, datetime, timedelta

import pytest

from siteops_agent.log_parser.base import LogParser
from siteops_agent.log_source.file import FileLogSource
from siteops_agent.models import LogEvent

T0 = datetime(2026, 11, 1, 7, tzinfo=UTC)
DAY = (T0, T0 + timedelta(days=1))
LINE = "2026-11-01T07:00:00.000Z ERROR log-02 rsyslogd: No space left on device"


def test_malformed_lines_are_counted_whatever_their_time_and_blank_lines_skipped(tmp_path) -> None:
    path = tmp_path / "site.log"
    path.write_text(f"{LINE}\nnot a log line\n\n2026-11-01T07:00:01 INFO h1 app: no zone\n", encoding="utf-8")

    batch = FileLogSource(path).read(*DAY)

    assert [e.raw for e in batch.events] == [LINE]
    assert batch.unparsable == 2  # an unparsable line has no time, so the range can't exclude it


def test_crlf_line_endings_are_not_part_of_the_event(tmp_path) -> None:
    path = tmp_path / "site.log"
    path.write_bytes(f"{LINE}\r\n".encode())

    [event] = FileLogSource(path).read(*DAY).events

    assert event.raw == LINE


def test_undecodable_bytes_do_not_stop_the_read(tmp_path) -> None:
    path = tmp_path / "site.log"
    path.write_bytes(LINE.encode() + b" \xff\xfe\n" + LINE.encode() + b"\n")

    batch = FileLogSource(path).read(*DAY)

    assert len(batch.events) == 2 and batch.unparsable == 0
    assert batch.events[0].message.endswith("��")  # replaced, not dropped


def test_a_missing_file_is_an_error_not_an_empty_log(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        FileLogSource(tmp_path / "missing.log").read(*DAY)


def test_another_line_format_is_another_parser(tmp_path) -> None:
    # the source doesn't know the format: a JSON-lines parser, say, plugs in the same way
    class PipeParser(LogParser):
        def _parse_line(self, line: str) -> LogEvent | None:
            time, level, host, message = line.split("|")
            parsed_time, parsed_level = self.parse_time(time), self.parse_level(level)
            if parsed_time is None or parsed_level is None:
                return None
            return LogEvent(parsed_time, host, parsed_level, "app", message, {}, line)

    path = tmp_path / "site.log"
    path.write_text("2026-11-01T07:00:00Z|WARN|h1|slow\n", encoding="utf-8")

    [event] = FileLogSource(path, PipeParser()).read(*DAY).events

    assert (event.host, event.level, event.message) == ("h1", "WARN", "slow")
