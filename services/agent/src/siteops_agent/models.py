from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, get_args

Level = Literal["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]
LEVELS: frozenset[str] = frozenset(get_args(Level))


@dataclass(frozen=True)
class LogEvent:
    """One parsed log line (agent/9). No judgement on whether it matters: that is the prefilter's job."""

    time: datetime  # timezone-aware, UTC
    host: str
    level: Level
    component: str  # the service or daemon that wrote the line: "patroni", "public-api"
    message: str
    # key=value pairs found in the message: {"duration_ms": "1168"}. Left out of the hash: a mapping has none,
    # and it adds nothing (equal events have equal `raw`). Stored read-only, so the event really is frozen.
    fields: Mapping[str, str] = field(hash=False)
    raw: str  # the line as read, without its line ending: quoted as the ticket's evidence

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))  # a copy: the caller's dict can't change it


@dataclass(frozen=True)
class Window:
    """Where the LLM should look: one host, one rule, a bounded run of events (agent/9)."""

    host: str
    rule: str  # which rule fired: a hint for the agent, not a verdict
    start: datetime
    end: datetime
    events: tuple[LogEvent, ...]  # the matching events plus a little context before and after
    omitted: int = 0  # events left out by the size bound
