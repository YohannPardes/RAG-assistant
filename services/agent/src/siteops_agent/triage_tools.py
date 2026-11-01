"""One window's tools and memory: what the triage model can do while it investigates a window (agent/4, agent/10).

The model sees only each tool's arguments and the text it returns. Everything else is here, out of its reach: the
lines shown so far and their numbers, the runbook passages retrieved, the budgets, the clients. Plain Python, no
framework: tools.py hands these methods to LangChain.

Two kinds of failure, kept apart:
  - the model's mistake (bad arguments, a failed check, a budget used up): a ToolReply with error=True, written for
    the model to correct; the window goes on;
  - ours (rag or the tickets service down, a bug): ToolFailure is raised, the window ends as "error". The model is
    never asked to fix what it can't.
"""

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from siteops_agent.decision import Dismissal, TicketDraft, check_draft, to_ticket_create
from siteops_agent.log_context import LogContext, LogContextError, LogQuery
from siteops_agent.models import Window
from siteops_agent.prompts import ShownLines, render_context
from siteops_agent.rag_client.base import RagClient, RagError
from siteops_agent.report import Search
from siteops_agent.ticket_sink.base import TicketSink, TicketSinkError
from siteops_common.contracts import CreateTicketResult, SearchHit, SearchRequest

# the draft's own bound on evidence_lines, read from it: one source for the limit and the message about it
MAX_EVIDENCE: int = next(rule.max_length for rule in TicketDraft.model_fields["evidence_lines"].metadata if hasattr(rule, "max_length"))


@dataclass(frozen=True)
class ToolLimits:
    """The budgets of one window (agent/4). The values come from the config, through the composition root."""

    max_searches: int
    max_log_queries: int
    hits_per_search: int

    def __post_init__(self) -> None:
        if self.max_searches < 1 or self.max_log_queries < 0 or self.hits_per_search < 1:
            raise ValueError("a window needs at least one search, returning at least one hit")


class RunbookQuery(BaseModel):
    """search_runbooks' arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=300, description="What the logs show, in a few words.")


@dataclass(frozen=True)
class ToolReply:
    """What the model reads back. error=True: its call was refused, and the text says why."""

    text: str
    error: bool = False


class ToolFailure(Exception):
    """Our failure, not the model's: the window ends as "error" with this message (agent/4)."""

    def __init__(self, service: str, message: str) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service


@dataclass(frozen=True)
class Ticketed:
    draft: TicketDraft
    result: CreateTicketResult  # result.created is False when the tickets service deduplicated it (agent/3)


@dataclass(frozen=True)
class Dismissed:
    dismissal: Dismissal


Decision = Ticketed | Dismissed


@dataclass(frozen=True)
class LogLookup:
    """One log_context call, for the report and the traces."""

    query: dict[str, Any]
    events: int
    omitted: int


class TriageTools:
    """The four tools of one window, and what they remember. Made fresh for every window."""

    def __init__(
        self,
        window: Window,
        rag: RagClient,
        context: LogContext,
        sink: TicketSink,
        known_hosts: Set[str],  # every host that wrote a line in the run: the cause must be one of them
        limits: ToolLimits,
    ) -> None:
        self.window = window
        self._rag, self._context, self._sink = rag, context, sink
        self._known_hosts = known_hosts
        self.limits = limits
        self.shown = ShownLines(window)  # every line shown to the model, numbered
        self.retrieved: dict[str, SearchHit] = {}  # every passage search returned, by chunk_id: what may be cited
        self.searches: list[Search] = []
        self.log_lookups: list[LogLookup] = []
        self.check_errors: list[str] = []  # refused create_ticket calls, in order
        self.decision: Decision | None = None  # set by create_ticket or dismiss: the window is done

    # --- the tools ---

    def search_runbooks(self, args: Mapping[str, Any]) -> ToolReply:
        """Runbook passages for what the logs show. Raises ToolFailure if rag can't answer."""
        if refused := self._refuse_when_decided():
            return refused
        if len(self.searches) >= self.limits.max_searches:
            return ToolReply(f"search budget used ({self.limits.max_searches} searches): decide with the passages you have", error=True)
        try:
            query = RunbookQuery.model_validate(args)
        except ValidationError as error:
            return ToolReply(_explain(error), error=True)
        try:
            hits = self._rag.search(SearchRequest(query=query.query, top_k=self.limits.hits_per_search)).hits
        except RagError as error:
            raise ToolFailure("rag", str(error)) from error
        self.retrieved.update({hit.chunk_id: hit for hit in hits})
        self.searches.append(Search(query=query.query, chunk_ids=[hit.chunk_id for hit in hits], sources=[hit.source for hit in hits]))
        if not hits:
            return ToolReply("No runbook passage matches.")
        return ToolReply("\n\n".join(_render_hit(hit) for hit in hits))

    def log_context(self, args: Mapping[str, Any]) -> ToolReply:
        """More log events around the window, from this run's events (agent/14). Its lines are numbered after the window's."""
        if refused := self._refuse_when_decided():
            return refused
        if len(self.log_lookups) >= self.limits.max_log_queries:
            return ToolReply(f"log_context budget used ({self.limits.max_log_queries} calls): decide with the lines you have", error=True)
        try:
            query = LogQuery.model_validate(args)
            result = self._context.around(self.window, query)
        except ValidationError as error:
            return ToolReply(_explain(error), error=True)
        except LogContextError as error:  # an invalid or too slow pattern: the model can rewrite it
            return ToolReply(str(error), error=True)
        numbered = self.shown.add(result.events)
        self.log_lookups.append(LogLookup(query=query.model_dump(exclude_defaults=True), events=len(result.events), omitted=result.omitted))
        return ToolReply(render_context(numbered, result.omitted))

    def create_ticket(self, args: Mapping[str, Any]) -> ToolReply:
        """Check the model's draft, build the ticket from the facts, file it. Raises ToolFailure if tickets can't file it."""
        if refused := self._refuse_when_decided():
            return refused
        try:
            draft = TicketDraft.model_validate(args)
        except ValidationError as error:
            return self._refuse_draft(_explain(error))
        if problems := check_draft(draft, self.shown, self.retrieved, self._known_hosts):
            return self._refuse_draft("; ".join(problems))
        ticket = to_ticket_create(draft, self.shown, self.retrieved, self._known_hosts)
        try:
            result = self._sink.create(ticket)
        except TicketSinkError as error:
            raise ToolFailure("tickets", str(error)) from error
        self.decision = Ticketed(draft, result)
        if result.created:
            return ToolReply(f"Ticket {result.ticket.id} opened. The window is done.")
        return ToolReply(f"Already open as {result.ticket.id} (the same fault on the same host). The window is done.")

    def dismiss(self, args: Mapping[str, Any]) -> ToolReply:
        """Close the window without a ticket."""
        if refused := self._refuse_when_decided():
            return refused
        try:
            dismissal = Dismissal.model_validate(args)
        except ValidationError as error:
            return ToolReply(_explain(error), error=True)
        self.decision = Dismissed(dismissal)
        return ToolReply("Dismissed. The window is done.")

    # --- helpers ---

    def _refuse_when_decided(self) -> ToolReply | None:
        if self.decision is None:
            return None
        return ToolReply("This window is already decided: stop.", error=True)

    def _refuse_draft(self, why: str) -> ToolReply:
        self.check_errors.append(why)
        return ToolReply(f"Ticket refused: {why}. Fix it and call create_ticket again, or dismiss.", error=True)


def _render_hit(hit: SearchHit) -> str:
    return f"chunk_id={hit.chunk_id}\nsource={hit.source}\nsection={' > '.join(hit.heading_path)}\n{hit.text}"


def _explain(error: ValidationError) -> str:
    """Pydantic's errors in words the model can act on; the one the probe saw most gets its own sentence."""
    reasons = []
    for problem in error.errors():
        field = ".".join(str(part) for part in problem["loc"]) or "arguments"
        if field == "evidence_lines" and problem["type"] == "too_long":
            reasons.append(f"evidence_lines: cite at most {MAX_EVIDENCE} lines, the clearest ones")
        else:
            reasons.append(f"{field}: {problem['msg']}")
    return "; ".join(reasons)
