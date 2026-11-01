"""What a triage run reports (agent/4): one outcome per window, so the eval can score it and a miss can be traced.

The eval reads it instead of the tickets store: each outcome keeps its window's host, rule and span, so a missed
fault is blamed on the prefilter (no window overlaps it) or on the model (a window did, and was dismissed or misdiagnosed).
"""

from collections import Counter
from typing import Any, Literal, Self, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, model_validator

from siteops_agent.decision import Dismissal, TicketDraft
from siteops_agent.models import Window

Status = Literal[
    "ticketed",  # a new ticket was opened
    "deduplicated",  # create_ticket returned an existing ticket (agent/3)
    "dismissed",  # the model closed the window without a ticket
    "unresolved",  # the window's budget ran out with no decision: never ticketed (agent/4)
    "error",  # a call failed (rag, tickets, the model): the window was not decided
]


class _Report(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Search(_Report):
    """One search_runbooks call: what the model asked, what it got back."""

    query: str
    chunk_ids: list[str]
    sources: list[str]  # the runbooks of those chunks, best first: whether the right one was even offered


class WindowOutcome(_Report):
    """How one window ended, and enough of how it got there to debug it."""

    window_id: str
    host: str
    rule: str
    start: AwareDatetime
    end: AwareDatetime
    events: int  # lines the model saw
    omitted: int
    status: Status
    draft: TicketDraft | None = None  # what the model filed (ticketed / deduplicated)
    ticket_id: str | None = None
    dismissal: Dismissal | None = None
    searches: list[Search] = Field(default_factory=list)
    check_errors: list[str] = Field(default_factory=list)  # rejected create_ticket calls, in order
    llm_calls: int = 0
    duration_s: float = 0.0
    error: str | None = None  # status "error": what failed

    @classmethod
    def of(cls, window: Window, status: Status, **details: Any) -> Self:
        """The outcome of `window`: its identity and span, plus how it ended."""
        return cls(
            window_id=window.id,
            host=window.host,
            rule=window.rule,
            start=window.start,
            end=window.end,
            events=len(window.events),
            omitted=window.omitted,
            status=status,
            **details,
        )

    @model_validator(mode="after")
    def _status_has_its_details(self) -> Self:
        ticket = self.draft is not None and self.ticket_id is not None
        expected = {
            "ticketed": ticket,
            "deduplicated": ticket,
            "dismissed": self.dismissal is not None,
            "unresolved": True,
            "error": self.error is not None,
        }
        if not expected[self.status]:
            raise ValueError(f"status {self.status!r} without its details")
        return self


class TriageReport(_Report):
    """One run over [since, until)."""

    run_id: str
    since: AwareDatetime
    until: AwareDatetime
    model: str  # the model that decided: runs are compared per model
    events_read: int
    unparsable: int  # a jump in it is itself a signal (agent/9)
    outcomes: list[WindowOutcome]

    @computed_field  # type: ignore[prop-decorator]  # pydantic's documented pattern; mypy can't type a decorated property
    @property
    def totals(self) -> dict[str, int]:
        """Outcomes by status, every status present (0 included): the first thing to read."""
        counts = Counter(outcome.status for outcome in self.outcomes)
        return {status: counts[status] for status in get_args(Status)}

    @model_validator(mode="before")
    @classmethod
    def _totals_are_recomputed(cls, data: Any) -> Any:
        # written to the JSON for its readers; reading a report back recomputes them instead of trusting the file
        return {key: value for key, value in data.items() if key != "totals"} if isinstance(data, dict) else data

    @model_validator(mode="after")
    def _range_in_order(self) -> Self:
        if self.until < self.since:
            raise ValueError("until is before since")
        return self
