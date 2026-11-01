import re
from datetime import UTC, datetime, timedelta

import pytest

from siteops_agent.log_parser.syslog import SyslogStyleParser
from siteops_agent.models import Level, LogEvent
from siteops_agent.prefilter import MAX_EVENTS, RULES, Rule, find_windows

T0 = datetime(2026, 11, 1, 7, tzinfo=UTC)
START = datetime(2026, 11, 1, 6, tzinfo=UTC)
# the rule that must catch each seeded fault (agent/12)
FAULT_RULES = {
    "disk_full": "disk_pressure",
    "pg_primary_down": "db_leader_unreachable",
    "pg_replica_lag": "replica_lag",
    "cert_expiry": "tls_certificate",
    "high_latency": "slow_requests",
    "api_error_rate": "http_5xx",
    "pod_oomkilled": "pod_crashloop",
}


def ev(seconds: float, message: str = "ok", level: Level = "INFO", host: str = "h1", component: str = "app") -> LogEvent:
    t = T0 + timedelta(seconds=seconds)
    return LogEvent(t, host, level, component, message, {}, f"{t.isoformat()} {level} {host} {component}: {message}")


def rule(name: str = "boom", min_count: int = 1, within_minutes: float = 5, min_level: Level = "WARN", components: str | None = None) -> Rule:
    found_in = frozenset(components.split()) if components else None
    within = timedelta(minutes=within_minutes)
    return Rule(name=name, pattern=re.compile("boom"), components=found_in, min_level=min_level, min_count=min_count, within=within)


# --- matching ---


def test_a_rule_matches_on_level_component_and_pattern() -> None:
    boom = rule(components="app")

    assert boom.matches(ev(0, "boom", "WARN"))
    assert boom.matches(ev(0, "a boom here", "CRITICAL"))  # above min_level, pattern searched anywhere
    assert not boom.matches(ev(0, "boom", "INFO"))
    assert not boom.matches(ev(0, "boom", "WARN", component="other"))
    assert not boom.matches(ev(0, "fine", "WARN"))


def test_an_event_goes_to_the_first_matching_rule_only() -> None:
    windows = find_windows([ev(0, "boom", "ERROR")], [rule("first"), rule("second")])

    assert [w.rule for w in windows] == ["first"]


def test_a_rule_is_refused_without_a_count_or_a_span() -> None:
    with pytest.raises(ValueError):
        rule(min_count=0)
    with pytest.raises(ValueError):
        rule(within_minutes=0)


def test_rules_sharing_a_name_are_refused() -> None:
    # matches are grouped by rule name: two rules with one name would give every window twice
    with pytest.raises(ValueError, match="unique"):
        find_windows([ev(0, "boom", "WARN")], [rule("same"), rule("same", min_level="ERROR")])


def test_no_events_give_no_windows() -> None:
    assert find_windows([]) == []


# --- pattern rules: grouping and merging ---


def test_one_matching_line_opens_a_window() -> None:
    [window] = find_windows([ev(0), ev(10, "boom", "WARN"), ev(20)], [rule()])

    assert (window.host, window.rule, window.start, window.end) == ("h1", "boom", T0 + timedelta(seconds=10), T0 + timedelta(seconds=10))


def test_matches_merge_while_the_gap_is_at_most_within() -> None:
    events = [ev(0, "boom", "WARN"), ev(300, "boom", "WARN"), ev(301 + 300, "boom", "WARN")]  # gaps: exactly 5 min, then 5 min 1 s

    windows = find_windows(events, [rule(within_minutes=5)])

    assert [(w.start, w.end) for w in windows] == [(events[0].time, events[1].time), (events[2].time, events[2].time)]


def test_windows_are_per_host_and_per_rule() -> None:
    events = [ev(0, "boom", "WARN", host="h1"), ev(1, "boom", "WARN", host="h2"), ev(2, "bang", "WARN", host="h1")]
    bang = Rule(name="bang", pattern=re.compile("bang"), min_level="WARN", within=timedelta(minutes=5))

    windows = find_windows(events, [rule(), bang])

    assert [(w.host, w.rule) for w in windows] == [("h1", "boom"), ("h2", "boom"), ("h1", "bang")]


# --- threshold rules ---


def test_a_threshold_rule_needs_min_count_matches_within_its_span() -> None:
    below = [ev(0, "boom", "WARN"), ev(60, "boom", "WARN")]
    sparse = [ev(0, "boom", "WARN"), ev(240, "boom", "WARN"), ev(480, "boom", "WARN")]  # merged (gaps 4 min), but never 3 in 5 min

    assert find_windows(below, [rule(min_count=3)]) == []
    assert find_windows(sparse, [rule(min_count=3)]) == []


def test_a_threshold_window_starts_at_the_run_that_reached_the_count() -> None:
    events = [ev(0, "boom", "WARN"), ev(290, "boom", "WARN"), ev(500, "boom", "WARN"), ev(510, "boom", "WARN"), ev(520, "boom", "WARN")]

    [window] = find_windows(events, [rule(min_count=3)])

    # 0, 290 and 500 are in one run (gaps under 5 min) but span 8 min; 290, 500, 510 is the first 3 within 5 min
    assert (window.start, window.end) == (events[1].time, events[4].time)


def test_an_event_a_threshold_rule_matched_does_not_fall_through_to_a_later_rule() -> None:
    # agent/12: consumed even when the count is never reached
    windows = find_windows([ev(0, "boom", "ERROR")], [rule(min_count=3), rule("unknown", min_level="ERROR")])

    assert windows == []


def test_matches_exactly_within_apart_reach_the_count() -> None:
    events = [ev(0, "boom", "WARN"), ev(150, "boom", "WARN"), ev(300, "boom", "WARN")]  # first to third: exactly 5 min

    assert len(find_windows(events, [rule(min_count=3)])) == 1


def test_two_runs_that_each_reach_the_count_give_two_windows() -> None:
    burst = [0, 10, 20]
    events = [ev(t, "boom", "WARN") for t in burst] + [ev(1000 + t, "boom", "WARN") for t in burst]

    windows = find_windows(events, [rule(min_count=3)])

    assert [(w.start, w.end) for w in windows] == [(events[0].time, events[2].time), (events[3].time, events[5].time)]


def test_earlier_matches_that_did_not_fire_are_context_of_a_late_threshold_window() -> None:
    early = [ev(0, "boom", "WARN"), ev(290, "boom", "WARN")]  # same run as the burst (gaps under 5 min), but no 3 of them within 5 min
    burst = [ev(590 + t, "boom", "WARN") for t in (0, 10, 20)]

    [window] = find_windows([*early, *burst], [rule(min_count=3)])

    assert window.start == burst[0].time
    assert window.events == (*early, *burst)


# --- what a window holds ---


def test_a_window_holds_context_from_its_own_host_and_every_event_in_between() -> None:
    before = [ev(i) for i in range(7)]
    middle = [ev(10, "boom", "WARN"), ev(11), ev(12, "boom", "WARN")]
    after = [ev(20 + i) for i in range(4)]
    elsewhere = [ev(11.5, "noise", host="h2")]

    [window] = find_windows(before + middle + after + elsewhere, [rule()])

    assert window.events == tuple(before[-5:] + middle + after[:2])
    assert window.omitted == 0


def test_context_stops_at_the_start_and_end_of_the_log() -> None:
    events = [ev(0), ev(1, "boom", "WARN")]

    [window] = find_windows(events, [rule()])

    assert window.events == tuple(events)


def test_a_long_window_keeps_its_first_and_last_events() -> None:
    events = [ev(i, "boom", "WARN") for i in range(MAX_EVENTS + 10)]

    [window] = find_windows(events, [rule()])

    assert len(window.events) == MAX_EVENTS and window.omitted == 10
    assert window.events == tuple(events[: MAX_EVENTS // 2] + events[-MAX_EVENTS // 2 :])
    assert (window.start, window.end) == (events[0].time, events[-1].time)  # the span is the whole run, not what was kept


def test_a_window_of_exactly_max_events_omits_nothing() -> None:
    events = [ev(i, "boom", "WARN") for i in range(MAX_EVENTS)]

    [window] = find_windows(events, [rule()])

    assert window.events == tuple(events) and window.omitted == 0


def test_the_cap_counts_the_context_events() -> None:
    events = [ev(i) for i in range(5)] + [ev(5 + i, "boom", "WARN") for i in range(MAX_EVENTS - 4)] + [ev(100), ev(101)]

    [window] = find_windows(events, [rule()])

    assert window.omitted == 3  # 5 before + 46 matches + 2 after = 53
    assert window.events[0] == events[0] and window.events[-1] == events[-1]


def test_an_event_of_one_rule_is_context_in_another_rules_window() -> None:
    # e.g. slow requests and a 504 timeout on the same API host
    boom, bang = ev(0, "boom", "WARN"), ev(1, "bang", "ERROR")
    bang_rule = Rule(name="bang", pattern=re.compile("bang"), min_level="WARN", within=timedelta(minutes=5))

    windows = find_windows([boom, bang], [rule(), bang_rule])

    assert [(w.rule, w.events) for w in windows] == [("boom", (boom, bang)), ("bang", (boom, bang))]


def test_identical_and_simultaneous_lines_are_all_kept_in_input_order() -> None:
    # events are hashable, but never de-duplicated: two identical lines are two occurrences
    events = [ev(0, "a"), ev(0, "boom", "WARN"), ev(0, "boom", "WARN"), ev(0, "b")]

    [window] = find_windows(events, [rule()])

    assert window.events == tuple(events)


# --- determinism ---


def test_windows_are_sorted_by_start_and_input_order_does_not_matter() -> None:
    events = [ev(100, "boom", "WARN", host="h2"), ev(0, "boom", "WARN", host="h3"), ev(50, "boom", "WARN", host="h1"), ev(0, "x")]

    windows = find_windows(events, [rule(within_minutes=0.1)])

    assert [w.host for w in windows] == ["h3", "h1", "h2"]
    assert find_windows(list(reversed(events)), [rule(within_minutes=0.1)]) == windows


def test_info_noise_gives_no_windows() -> None:
    assert find_windows([ev(i, "boom") for i in range(100)], RULES) == []


# --- agent/12's rules on the generator's logs ---


P = "shop/checkout-worker-7d9f8-x2k4q"


@pytest.mark.parametrize(
    ("component", "level", "message", "expected"),
    [
        ("public-api", "ERROR", "POST /v2/orders 500 orders-db write failed: FATAL: the database system is in recovery mode", "db_write_failed"),
        ("public-api", "ERROR", "POST /v2/orders 500 unhandled exception: KeyError: 'currency'", "http_5xx"),
        ("public-api", "ERROR", "GET /v2/search 504 upstream timeout after 5000ms", "unknown"),
        ("public-api", "WARN", "POST /v2/orders 201 1450ms", "slow_requests"),
        ("public-api", "INFO", "GET /v2/orders 200 41ms", None),
        ("disk-monitor", "WARN", "/var/log usage 84%", None),
        ("disk-monitor", "WARN", "/var/log usage 85%", "disk_pressure"),
        ("disk-monitor", "WARN", "/var/log usage 100%", "disk_pressure"),
        ("rsyslogd", "ERROR", "action 'omfile' suspended: write error: No space left on device (errno 28)", "disk_pressure"),
        ("pg-monitor", "INFO", "replica lag 300 MB, replay_lag 150.0s", None),
        ("pg-monitor", "WARN", "replica lag 70 MB, replay_lag 10.0s", "replica_lag"),
        ("pg-monitor", "ERROR", "replica lag 300 MB, replay_lag 150.0s", "replica_lag"),
        ("patroni", "WARN", "leader db-primary-01 unreachable; no automatic failover, cluster paused", "db_leader_unreachable"),
        ("patroni", "INFO", "no action. I am (db-primary-01), the leader with the lock", None),
        ("synthetics", "ERROR", "check https://api.example.com/healthz failed: x509: certificate has expired or is not yet valid", "tls_certificate"),
        ("ingress-nginx", "WARN", "SSL_do_handshake() failed (SSL: bad certificate) while SSL handshaking", "tls_certificate"),
        ("synthetics", "WARN", "check https://api.example.com/healthz timeout after 5000ms, retrying", None),
        ("kubelet", "WARN", f"Container checkout-worker in pod {P} OOMKilled (memory limit 512Mi), restart count 1", "pod_crashloop"),
        ("kubelet", "WARN", f"Container checkout-worker in pod {P} restarted (reason=Error, exitCode=1), restart 1", None),
        ("kernel", "CRITICAL", "Kernel panic - not syncing", "unknown"),
        ("kernel", "WARN", "something odd", None),
    ],
)
def test_which_rule_takes_a_line(component, level, message, expected) -> None:
    # the first matching rule takes the event (agent/12's order): e.g. a database outage on an API host is not a bad release
    event = ev(0, message, level, component=component)

    assert next((r.name for r in RULES if r.matches(event)), None) == expected


def test_each_decoy_on_its_own_opens_no_window_but_the_504(generate_logs) -> None:
    for decoy in generate_logs.DECOYS:
        lines = [generate_logs.format_line(line) for line in decoy(START)]
        events, _ = SyslogStyleParser().parse_lines(lines)

        windows = find_windows(events)

        if any("504" in line for line in lines):
            assert [w.rule for w in windows] == ["unknown"], lines
        else:
            assert windows == [], lines


@pytest.fixture(scope="module", params=[1, 7, 42])
def generated(request, generate_logs):
    lines, faults = generate_logs.generate(seed=request.param, start=START, hours=4)
    events, _ = SyslogStyleParser().parse_lines(generate_logs.format_line(line) for line in lines)
    return find_windows(events), faults


def overlaps(window, start: datetime, end: datetime) -> bool:
    return window.start <= end and window.end >= start


def test_every_seeded_fault_gets_a_window_from_its_rule(generated) -> None:
    # prefilter recall is 1: a miss here can't be blamed on the LLM (scope/4)
    windows, faults = generated

    for fault in faults:
        start, end = datetime.fromisoformat(fault["start"]), datetime.fromisoformat(fault["end"])
        found = [w for w in windows if w.rule == FAULT_RULES[fault["fault_type"]] and w.host in fault["affected_hosts"] and overlaps(w, start, end)]
        assert found, f"no {FAULT_RULES[fault['fault_type']]} window for {fault['id']} {fault['fault_type']}"


def test_outside_the_faults_only_the_504_decoy_opens_a_window(generated) -> None:
    windows, faults = generated
    margin = timedelta(minutes=1)  # the 2 context events after a fault's last line
    spans = [(datetime.fromisoformat(f["start"]) - margin, datetime.fromisoformat(f["end"]) + margin) for f in faults]

    outside = [w for w in windows if not any(overlaps(w, start, end) for start, end in spans)]

    assert outside, "the ERROR 504 decoy should open an unknown window for the agent to dismiss"
    assert all(w.rule == "unknown" and any("504 upstream timeout" in e.message for e in w.events) for w in outside), outside
