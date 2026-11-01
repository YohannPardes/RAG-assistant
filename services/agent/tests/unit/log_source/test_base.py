"""Contract tests: one suite written against `LogSource`, run once per implementation (tooling/5)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest

from siteops_agent.log_parser.syslog import SyslogStyleParser
from siteops_agent.log_source.base import LogSource
from siteops_agent.log_source.file import FileLogSource
from siteops_agent.log_source.memory import MemoryLogSource

T0 = datetime(2026, 11, 1, 7, tzinfo=UTC)


def line(seconds: int, message: str = "ok") -> str:
    return f"{(T0 + timedelta(seconds=seconds)).isoformat()} INFO h1 app: {message}"


Factory = Callable[[list[str]], LogSource]  # the log as lines → a source holding it


@pytest.fixture(params=["memory", "file"])
def make_source(request: pytest.FixtureRequest, tmp_path) -> Factory:
    if request.param == "memory":
        return lambda lines: MemoryLogSource(*SyslogStyleParser().parse_lines(lines))

    def file_source(lines: list[str]) -> LogSource:
        path = tmp_path / "site.log"
        path.write_text("".join(f"{text}\n" for text in lines), encoding="utf-8")
        return FileLogSource(path)

    return file_source


def test_only_events_in_the_half_open_range_are_read(make_source) -> None:
    source = make_source([line(0, "before"), line(10, "first"), line(20, "inside"), line(30, "at until")])

    batch = source.read(T0 + timedelta(seconds=10), T0 + timedelta(seconds=30))

    assert [e.message for e in batch.events] == ["first", "inside"]  # since included, until excluded


def test_events_come_in_time_order_and_ties_keep_the_source_order(make_source) -> None:
    source = make_source([line(20, "late"), line(10, "tie a"), line(0, "early"), line(10, "tie b")])

    batch = source.read(T0, T0 + timedelta(minutes=1))

    assert [e.message for e in batch.events] == ["early", "tie a", "tie b", "late"]


def test_the_same_range_read_twice_gives_the_same_batch(make_source) -> None:
    # what makes a triage run repeatable (agent/4)
    source = make_source([line(i, f"m{i}") for i in range(5)])

    assert source.read(T0, T0 + timedelta(seconds=3)) == source.read(T0, T0 + timedelta(seconds=3))


def test_records_that_give_no_event_are_counted(make_source) -> None:
    batch = make_source([line(0), "garbage", line(1), "more garbage"]).read(T0, T0 + timedelta(minutes=1))

    assert (len(batch.events), batch.unparsable) == (2, 2)


def test_an_empty_range_reads_nothing(make_source) -> None:
    assert make_source([line(0)]).read(T0, T0).events == ()


def test_times_without_a_zone_are_refused(make_source) -> None:
    source = make_source([line(0)])

    with pytest.raises(ValueError, match="timezone-aware"):
        source.read(T0.replace(tzinfo=None), T0 + timedelta(minutes=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        source.read(T0, (T0 + timedelta(minutes=1)).replace(tzinfo=None))


def test_a_reversed_range_is_refused(make_source) -> None:
    with pytest.raises(ValueError, match="after"):
        make_source([line(0)]).read(T0 + timedelta(minutes=1), T0)


def test_a_range_in_another_zone_is_the_same_instants(make_source) -> None:
    # event times are UTC; a caller in +02:00 means the same moments
    source = make_source([line(0, "in"), line(60, "out")])
    since = datetime(2026, 11, 1, 9, tzinfo=timezone(timedelta(hours=2)))  # = T0

    assert [e.message for e in source.read(since, since + timedelta(seconds=60)).events] == ["in"]
