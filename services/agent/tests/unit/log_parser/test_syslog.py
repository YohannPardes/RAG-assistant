from datetime import UTC, datetime

import pytest

from siteops_agent.log_parser.syslog import SyslogStyleParser

LINE = "2026-11-01T07:05:12.345Z ERROR log-02 rsyslogd: action 'omfile' suspended: No space left on device (errno 28)"


def parse(line: str):
    return SyslogStyleParser().parse_line(line)


def test_a_line_gives_its_parts() -> None:
    event = parse(LINE)

    assert event is not None
    assert event.time == datetime(2026, 11, 1, 7, 5, 12, 345000, tzinfo=UTC)
    assert (event.level, event.host, event.component) == ("ERROR", "log-02", "rsyslogd")


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"])
def test_every_level_parses(level) -> None:
    event = parse(f"2026-11-01T07:05:12Z {level} api-01 public-api: GET /v2/orders 200 41ms")

    assert event is not None and event.level == level


@pytest.mark.parametrize("level", ["NOTICE", "error", "WARNING", "-"])
def test_an_unknown_level_makes_the_line_malformed(level) -> None:
    # mapping it to INFO would hide a garbled line; lower case is not a known level either
    assert parse(f"2026-11-01T07:05:12Z {level} api-01 public-api: GET /v2/orders 200 41ms") is None


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-11-01T07:05:12Z", datetime(2026, 11, 1, 7, 5, 12, tzinfo=UTC)),
        ("2026-11-01T07:05:12.345Z", datetime(2026, 11, 1, 7, 5, 12, 345000, tzinfo=UTC)),
        ("2026-11-01T09:05:12+02:00", datetime(2026, 11, 1, 7, 5, 12, tzinfo=UTC)),
    ],
    ids=["seconds", "milliseconds", "offset"],
)
def test_the_time_is_utc_with_or_without_milliseconds(timestamp, expected) -> None:
    event = parse(f"{timestamp} INFO api-01 public-api: ok")

    assert event is not None
    assert event.time == expected and event.time.utcoffset() is not None
    assert event.time.tzinfo == UTC


def test_a_time_without_a_zone_makes_the_line_malformed() -> None:
    # guessing the zone would shift every --since comparison
    assert parse("2026-11-01T07:05:12 INFO api-01 public-api: ok") is None


def test_the_message_keeps_every_colon_after_the_component() -> None:
    event = parse(LINE)

    assert event is not None
    assert event.message == "action 'omfile' suspended: No space left on device (errno 28)"


def test_key_value_words_become_fields() -> None:
    event = parse("2026-11-01T07:05:12Z WARN api-gw-02 nginx: p99_latency_ms=1240 endpoint=/v2/orders")

    assert event is not None
    assert event.fields == {"p99_latency_ms": "1240", "endpoint": "/v2/orders"}


def test_equals_signs_inside_text_are_not_fields_and_the_message_is_unchanged() -> None:
    message = "retry when lag = 0, url=https://x.example/?a=b&c=d, x== y, key="
    event = parse(f"2026-11-01T07:05:12Z WARN api-01 public-api: {message}")

    assert event is not None
    assert event.message == message
    assert event.fields == {"url": "https://x.example/?a=b&c=d,"}  # a value runs to the next space


def test_a_repeated_key_keeps_its_first_value() -> None:
    event = parse("2026-11-01T07:05:12Z WARN api-01 public-api: status=500 retried status=200")

    assert event is not None and event.fields == {"status": "500"}


def test_a_message_without_fields_has_none() -> None:
    event = parse(LINE)

    assert event is not None and event.fields == {}


def test_raw_is_the_line_as_read_without_its_line_ending() -> None:
    padded = "2026-11-01T07:05:12Z WARN  api-gw-02 nginx: slow"  # aligned level column

    event = parse(padded + "\r\n")

    assert event is not None
    assert event.raw == padded and event.level == "WARN"


def test_an_empty_message_is_kept_empty() -> None:
    event = parse("2026-11-01T07:05:12Z INFO api-01 public-api:")

    assert event is not None and event.message == ""


@pytest.mark.parametrize(
    "line",
    [
        "",
        "garbage",
        "2026-11-01T07:05:12Z INFO api-01 public-api no colon after the component",
        "not-a-time INFO api-01 public-api: ok",
        "2026-13-45T99:99:99Z INFO api-01 public-api: ok",
        "2026-11-01T07:05:12Z INFO api-01: no component",
        "\x00\xff� binary junk: \x1b[31m",
    ],
    ids=["empty", "one-word", "no-colon", "bad-time", "impossible-time", "no-component", "binary"],
)
def test_a_malformed_line_gives_none_without_raising(line) -> None:
    assert parse(line) is None


def test_parse_lines_keeps_the_order_and_counts_unparsable_lines() -> None:
    lines = [
        "2026-11-01T07:00:00Z INFO api-01 public-api: first\n",
        "garbage\n",
        "\n",  # blank: skipped, not counted (the end of a file)
        "2026-11-01T07:00:01Z INFO api-02 public-api: second\n",
        "2026-11-01T07:00:02Z NOTICE api-03 public-api: unknown level\n",
    ]

    events, unparsable = SyslogStyleParser().parse_lines(lines)

    assert [event.message for event in events] == ["first", "second"]
    assert unparsable == 2


def test_parse_lines_of_nothing_gives_nothing() -> None:
    assert SyslogStyleParser().parse_lines([]) == ([], 0)
