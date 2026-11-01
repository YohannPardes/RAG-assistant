from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from siteops_agent.log_context import MAX_DURING, MAX_PATTERN, LogContext, LogContextError, LogQuery
from siteops_agent.models import Level, LogEvent, Window

T0 = datetime(2026, 11, 1, 9, tzinfo=UTC)


def ev(minutes: float, host: str = "h1", message: str = "ok", level: Level = "INFO", component: str = "app") -> LogEvent:
    t = T0 + timedelta(minutes=minutes)
    return LogEvent(t, host, level, component, message, {}, f"{t.isoformat()} {level} {host} {component}: {message}")


def window_over(start: float, end: float, host: str = "h1") -> Window:
    return Window(host, "unknown", T0 + timedelta(minutes=start), T0 + timedelta(minutes=end), ())


def messages(events) -> list[str]:
    return [e.message for e in events]


def test_it_takes_the_closest_matches_before_every_one_during_and_the_first_after() -> None:
    log = [ev(m, message=f"m{m}") for m in range(10)]  # one event a minute
    result = LogContext(log).around(window_over(4, 5), LogQuery(before=2, after=1))

    assert messages(result.events) == ["m2", "m3", "m4", "m5", "m6"]
    assert result.omitted == 0


def test_another_hosts_events_during_the_window_are_found() -> None:
    # the pg_primary_down case: an API-host window, the cause logged by the replica (agent/14)
    log = [ev(1, "api-01", "POST 500"), ev(1.5, "db-replica-02", "leader db-primary-01 unreachable", "WARN", "patroni"), ev(2, "api-01", "POST 500")]

    result = LogContext(log).around(window_over(1, 2, host="api-01"), LogQuery(host="db-replica-02", before=0, after=0))

    assert messages(result.events) == ["leader db-primary-01 unreachable"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (LogQuery(host="h2"), ["h2-warn", "h2-error"]),
        (LogQuery(components=["db"]), ["h2-error"]),
        (LogQuery(min_level="ERROR"), ["h2-error"]),
        (LogQuery(pattern="warn|error"), ["h2-warn", "h2-error"]),
        (LogQuery(pattern="(?i)H1"), ["h1-info"]),
        (LogQuery(host="h2", min_level="WARN", pattern="error"), ["h2-error"]),  # every filter must match
    ],
    ids=["host", "component", "level", "pattern", "case-insensitive", "all-together"],
)
def test_filters(query, expected) -> None:
    log = [ev(1, "h1", "h1-info"), ev(1, "h2", "h2-warn", "WARN"), ev(1, "h2", "h2-error", "ERROR", "db")]

    assert messages(LogContext(log).around(window_over(0, 2), query).events) == expected


def test_the_pattern_is_searched_in_the_message_not_the_whole_line() -> None:
    # format-agnostic (agent/13): the model needn't know where the host or level sit in a raw line
    log = [ev(1, "db-replica-02", "ok")]

    assert LogContext(log).around(window_over(0, 2), LogQuery(pattern="db-replica")).events == ()


def test_a_long_span_keeps_its_first_and_last_matches() -> None:
    log = [ev(i / 10, message=f"m{i}") for i in range(MAX_DURING + 10)]

    result = LogContext(log).around(window_over(0, 100), LogQuery(before=0, after=0))

    assert len(result.events) == MAX_DURING and result.omitted == 10
    assert result.events[0].message == "m0" and result.events[-1].message == f"m{MAX_DURING + 9}"


def test_events_come_in_time_order_whatever_the_input_order() -> None:
    log = [ev(3, message="c"), ev(1, message="a"), ev(2, message="b")]

    assert messages(LogContext(log).around(window_over(2, 2), LogQuery(before=5, after=5)).events) == ["a", "b", "c"]


@pytest.mark.parametrize(
    "fields",
    [{"before": 21}, {"after": -1}, {"pattern": ""}, {"pattern": "x" * (MAX_PATTERN + 1)}, {"components": ["a"] * 11}, {"window": "x"}],
    ids=["before-too-many", "after-negative", "empty-pattern", "pattern-too-long", "too-many-components", "unknown-field"],
)
def test_out_of_bounds_queries_are_refused(fields) -> None:
    with pytest.raises(ValidationError):
        LogQuery(**fields)


def test_an_invalid_pattern_is_an_error_the_model_can_read() -> None:
    with pytest.raises(LogContextError, match="invalid pattern"):
        LogContext([ev(1)]).around(window_over(0, 2), LogQuery(pattern="(unclosed"))


def test_classic_catastrophic_patterns_run_fast() -> None:
    # the model writes the pattern; `regex` resists the textbook exponential cases that hang `re`
    log = [ev(1, message="a" * 30 + "!") for _ in range(50)]

    for pattern in (r"^(a|a?)+$", r"(a+)+$", r"^(a*)*b$"):
        assert LogContext(log).around(window_over(0, 2), LogQuery(pattern=pattern)).events == ()


def test_a_query_over_its_time_budget_is_stopped(monkeypatch) -> None:
    # the second line of defence, for a slow pattern `regex` doesn't catch: the budget covers the whole query
    monkeypatch.setattr("siteops_agent.log_context.PATTERN_BUDGET_S", 0)

    with pytest.raises(LogContextError, match="too slow"):
        LogContext([ev(1)]).around(window_over(0, 2), LogQuery(pattern="ok"))
