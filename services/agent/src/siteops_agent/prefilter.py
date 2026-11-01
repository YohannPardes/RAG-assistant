"""Prefilter (agent/9): selects where the LLM looks. Pure: same events → same windows; no I/O, no LLM.

The rules are agent/12's. Each event goes to the first rule that matches it; a rule's matches on one host
form windows, which get a little context from the same host.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import get_args

from siteops_agent.models import Level, LogEvent, Window

CONTEXT_BEFORE = 5  # events of the same host before the first match
CONTEXT_AFTER = 2  # and after the last
MAX_EVENTS = 50  # per window: the first and the last half are kept, Window.omitted counts the rest

_RANK = {level: rank for rank, level in enumerate(get_args(Level))}  # DEBUG < INFO < WARN < ERROR < CRITICAL


@dataclass(frozen=True, kw_only=True)
class Rule:
    """One kind of signal. A pattern rule (`min_count=1`) fires on one line; a threshold rule fires
    when `min_count` matches fall within `within`. Matches merge into one window while the gap between them is at most `within`."""

    name: str
    pattern: re.Pattern[str]  # searched in LogEvent.message
    components: frozenset[str] | None = None  # None: any component
    min_level: Level
    min_count: int = 1
    within: timedelta

    def __post_init__(self) -> None:
        if self.min_count < 1 or self.within <= timedelta(0):
            raise ValueError(f"rule {self.name}: min_count must be at least 1 and within positive")

    def matches(self, event: LogEvent) -> bool:
        return (
            _RANK[event.level] >= _RANK[self.min_level]
            and (self.components is None or event.component in self.components)
            and self.pattern.search(event.message) is not None
        )


def _rule(name: str, pattern: str, min_level: Level, within_minutes: int, components: str | None = None, min_count: int = 1) -> Rule:
    found_in = frozenset(components.split()) if components else None
    within = timedelta(minutes=within_minutes)
    return Rule(name=name, pattern=re.compile(pattern), components=found_in, min_level=min_level, min_count=min_count, within=within)


RULES: tuple[Rule, ...] = (  # agent/12, in order: specific before generic, the catch-all last
    _rule("db_leader_unreachable", r"unreachable|Connection refused|no automatic failover", "WARN", 10, components="patroni"),
    _rule("db_write_failed", r"write failed|recovery mode", "ERROR", 5, components="public-api"),
    _rule("tls_certificate", r"certificate has expired|x509|SSL_do_handshake\(\) failed|renewal failed", "WARN", 10),
    _rule("disk_pressure", r"usage (8[5-9]|9\d|100)%|No space left on device", "WARN", 10),
    _rule("pod_crashloop", r"OOMKilled|CrashLoopBackOff", "WARN", 10, components="kubelet"),
    _rule("replica_lag", r"replica lag", "WARN", 5, components="pg-monitor", min_count=3),
    # an access line (method, path, status, duration): a 504 timeout has another shape and goes to `unknown`
    _rule("slow_requests", r"^\S+ \S+ \d{3} \d+ms$", "WARN", 5, components="public-api", min_count=5),
    _rule("http_5xx", r"\s50[0-3]\s", "ERROR", 5, components="public-api", min_count=3),
    _rule("unknown", r"", "ERROR", 5),  # any ERROR or CRITICAL no other rule took: a fault without a rule still reaches the agent
)


def find_windows(events: Iterable[LogEvent], rules: Sequence[Rule] = RULES) -> list[Window]:
    """The windows of `events`, sorted by start time. Input order doesn't matter."""
    names = [rule.name for rule in rules]
    if len(set(names)) != len(names):  # matches are grouped by name: two rules sharing one would give every window twice
        raise ValueError(f"rule names must be unique: {names}")
    by_host: dict[str, list[LogEvent]] = defaultdict(list)
    for event in sorted(events, key=lambda e: e.time):  # stable: events at the same time keep their order
        by_host[event.host].append(event)

    windows = []
    for host, host_events in by_host.items():
        matched: dict[str, list[int]] = defaultdict(list)  # rule name → positions in host_events
        for position, event in enumerate(host_events):
            if rule := next((rule for rule in rules if rule.matches(event)), None):
                matched[rule.name].append(position)
        for rule in rules:
            for run in _runs(host_events, matched[rule.name], rule):
                windows.append(_window(host, rule, host_events, run))
    return sorted(windows, key=lambda w: (w.start, w.host, w.rule))


def _runs(events: list[LogEvent], positions: list[int], rule: Rule) -> Iterator[list[int]]:
    """The positions split where the gap between two matches exceeds `within`; only the runs that fire."""
    run: list[int] = []
    for position in positions:
        if run and events[position].time - events[run[-1]].time > rule.within:
            if fired := _fired(events, run, rule):
                yield fired
            run = []
        run.append(position)
    if run and (fired := _fired(events, run, rule)):
        yield fired


def _fired(events: list[LogEvent], run: list[int], rule: Rule) -> list[int] | None:
    """The run from the first of the first `min_count` matches within `within`; None if it never gets there."""
    n = rule.min_count
    for last in range(n - 1, len(run)):
        if events[run[last]].time - events[run[last - n + 1]].time <= rule.within:
            return run[last - n + 1 :]
    return None


def _window(host: str, rule: Rule, events: list[LogEvent], run: list[int]) -> Window:
    first, last = run[0], run[-1]
    selected = events[max(0, first - CONTEXT_BEFORE) : last + CONTEXT_AFTER + 1]  # every event in between, matching or not
    omitted = max(0, len(selected) - MAX_EVENTS)
    if omitted:
        selected = selected[: MAX_EVENTS // 2] + selected[-(MAX_EVENTS - MAX_EVENTS // 2) :]
    return Window(host, rule.name, events[first].time, events[last].time, tuple(selected), omitted)
