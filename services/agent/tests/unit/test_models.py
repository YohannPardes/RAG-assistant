import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from siteops_agent.models import LogEvent, Window

TIME = datetime(2026, 11, 1, 7, 5, 12, tzinfo=UTC)
RAW = "2026-11-01T07:05:12Z WARN api-gw-02 nginx: p99_latency_ms=1240"


def event(fields: dict[str, str] | None = None) -> LogEvent:
    return LogEvent(TIME, "api-gw-02", "WARN", "nginx", "p99_latency_ms=1240", fields if fields is not None else {"p99_latency_ms": "1240"}, RAW)


def test_events_and_windows_can_be_hashed() -> None:
    # the prefilter may put events in sets or use them as keys
    window = Window("api-gw-02", "high_latency", TIME, TIME, events=(event(),))

    assert len({event(), event()}) == 1
    assert hash(window) == hash(Window("api-gw-02", "high_latency", TIME, TIME, events=(event(),)))


def test_events_with_different_fields_are_not_equal() -> None:
    # left out of the hash, but still compared
    assert event({"a": "1"}) != event({"a": "2"})


def test_the_fields_of_an_event_cannot_be_changed() -> None:
    with pytest.raises(TypeError):
        event().fields["p99_latency_ms"] = "1"  # type: ignore[index]


def test_changing_the_callers_dict_does_not_change_the_event() -> None:
    fields = {"p99_latency_ms": "1240"}
    logged = event(fields)

    fields["p99_latency_ms"] = "1"

    assert logged.fields == {"p99_latency_ms": "1240"}


def test_a_window_id_depends_on_host_rule_and_span_only() -> None:
    # stable across runs: the prefilter is pure, so rereading a range gives the same ids (agent/4)
    window = Window("api-gw-02", "high_latency", TIME, TIME + timedelta(minutes=5), events=(event(),))

    assert window.id == Window("api-gw-02", "high_latency", TIME, TIME + timedelta(minutes=5), events=()).id
    assert (
        len(
            {
                window.id,
                dataclasses.replace(window, host="api-gw-03").id,
                dataclasses.replace(window, rule="unknown").id,
                dataclasses.replace(window, end=TIME + timedelta(minutes=6)).id,
            }
        )
        == 4
    )
