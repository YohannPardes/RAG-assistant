"""More log events around a window, for the model's log_context tool (agent/14). Pure: no I/O, no LLM.

The query is on LogEvent fields, never on raw lines, so it works whatever the log source and format (agent/13).
It reads only the events this run already read: the same range read twice gives the same context.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import get_args

import regex  # unlike re, it has a timeout: the model writes the pattern
from pydantic import BaseModel, ConfigDict, Field

from siteops_agent.models import Level, LogEvent, Window

MAX_SIDE = 20  # events before, and after, the window
MAX_DURING = 30  # events from the window's own span: the first and the last half are kept
MAX_PATTERN = 200  # characters
PATTERN_BUDGET_S = 0.1  # for the whole query, every event included

_RANK = {level: rank for rank, level in enumerate(get_args(Level))}


class LogContextError(ValueError):
    """The query can't run: an invalid or too slow pattern. Written for the model to correct."""


class LogQuery(BaseModel):
    """Which events to add around the window. Every filter is optional; together they must all match."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str | None = Field(default=None, description="Only this host's events; null for every host.")
    components: list[str] | None = Field(default=None, max_length=10, description='Only these components, e.g. ["patroni"].')
    min_level: Level | None = Field(default=None, description="Only events at this level or above.")
    pattern: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_PATTERN,
        description="A regular expression searched in each event's message (not the whole line); (?i) for case-insensitive.",
    )
    before: int = Field(default=10, ge=0, le=MAX_SIDE, description="How many matching events to add before the window.")
    after: int = Field(default=10, ge=0, le=MAX_SIDE, description="How many matching events to add after the window.")


@dataclass(frozen=True)
class ContextResult:
    events: tuple[LogEvent, ...]  # in time order
    omitted: int = 0  # matching events of the window's span left out by MAX_DURING


class LogContext:
    """The events of one run, queried around a window."""

    def __init__(self, events: Sequence[LogEvent]) -> None:
        self._events = sorted(events, key=lambda event: event.time)  # stable: ties keep the source's order

    def around(self, window: Window, query: LogQuery) -> ContextResult:
        """The matching events: up to `before` before the window, every one in its span (capped), up to `after` after it."""
        matches = self._matching(query)
        earlier = [event for event in matches if event.time < window.start]
        before = earlier[len(earlier) - query.before :] if query.before else []  # the closest ones
        during = [event for event in matches if window.start <= event.time <= window.end]
        after = [event for event in matches if event.time > window.end][: query.after]
        omitted = max(0, len(during) - MAX_DURING)
        if omitted:
            during = during[: MAX_DURING // 2] + during[-(MAX_DURING - MAX_DURING // 2) :]
        return ContextResult(tuple(before + during + after), omitted)

    def _matching(self, query: LogQuery) -> list[LogEvent]:
        pattern = _compile(query.pattern)
        deadline = time.monotonic() + PATTERN_BUDGET_S
        matches = []
        for event in self._events:
            if query.host is not None and event.host != query.host:
                continue
            if query.components is not None and event.component not in query.components:
                continue
            if query.min_level is not None and _RANK[event.level] < _RANK[query.min_level]:
                continue
            if pattern is not None and not _search(pattern, event.message, deadline):
                continue
            matches.append(event)
        return matches


def _compile(pattern: str | None) -> regex.Pattern[str] | None:
    if pattern is None:
        return None
    try:
        return regex.compile(pattern)
    except regex.error as error:
        raise LogContextError(f"invalid pattern {pattern!r}: {error}") from error


def _search(pattern: regex.Pattern[str], message: str, deadline: float) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LogContextError(f"pattern {pattern.pattern!r} is too slow: simplify it")
    try:
        return pattern.search(message, timeout=remaining) is not None
    except TimeoutError as error:
        raise LogContextError(f"pattern {pattern.pattern!r} is too slow: simplify it") from error
