"""What the triage model decides for a window, and the checks that turn it into a ticket (agent/10).

The model fills the judgement (`TicketDraft`, `Dismissal`); code checks it against the window and the searches it
made (`check_draft`), then fills the facts (`to_ticket_create`). These models are the narrow tools' schemas:
the model never sees `TicketCreate`.
"""

import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from siteops_agent.models import Window
from siteops_agent.prompts import line_event
from siteops_common.contracts import FaultType, RunbookRef, SearchHit, Severity, TicketCreate

MAX_EVIDENCE_CHARS = 2000  # TicketCreate's bound on one evidence line


class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TicketDraft(_Decision):
    """A ticket as the model writes it: judgement only. Bounds match TicketCreate's."""

    fault_type: FaultType = Field(description='The diagnosed fault; "other" for a real fault none of the types fits.')
    host: str = Field(min_length=1, max_length=255, description="The root-cause host. It must appear in the window's lines.")
    severity: Severity
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000, description="What happened, its impact, and why this diagnosis.")
    suggested_fix: str = Field(min_length=1, max_length=4000, description="Remediation steps for a human, from the cited runbook.")
    evidence_lines: list[int] = Field(min_length=1, max_length=20, description="Numbers of the window's lines that show the fault.")
    runbook_chunk_id: str | None = Field(default=None, description="A chunk_id returned by search_runbooks, or null if none fits.")


class Dismissal(_Decision):
    """No ticket: the window's events are transient or harmless."""

    reason: str = Field(min_length=1, max_length=1000, description="Why no human action is needed.")
    looks_like: FaultType | None = Field(default=None, description="The fault it resembles, if any.")  # helps debug decoys


def check_draft(draft: TicketDraft, window: Window, retrieved: Mapping[str, SearchHit]) -> list[str]:
    """What is wrong with the draft, written for the model to correct; empty when it can become a ticket.

    `retrieved` holds the hits of the searches made for this window, by chunk_id.
    """
    problems = []
    if not _host_in_window(draft.host, window):
        problems.append(f"host {draft.host!r} does not appear in the window's lines: name a host that does")
    if missing := [number for number in draft.evidence_lines if line_event(window, number) is None]:
        problems.append(f"evidence_lines {missing} do not exist: the window has lines 1 to {len(window.events)}")
    if draft.runbook_chunk_id is not None and draft.runbook_chunk_id not in retrieved:
        problems.append(f"runbook_chunk_id {draft.runbook_chunk_id!r} was not returned by search_runbooks: use one that was, or null")
    return problems


def to_ticket_create(draft: TicketDraft, window: Window, retrieved: Mapping[str, SearchHit]) -> TicketCreate:
    """The ticket for a checked draft: the model's judgement plus the facts, which code fills (agent/10)."""
    if problems := check_draft(draft, window, retrieved):
        raise ValueError(f"the draft fails its checks: {problems}")
    numbers = list(dict.fromkeys(draft.evidence_lines))  # the model's order, each line once
    evidence = [_quote(event.raw) for number in numbers if (event := line_event(window, number)) is not None]
    hit = retrieved.get(draft.runbook_chunk_id) if draft.runbook_chunk_id is not None else None
    return TicketCreate(
        fault_type=draft.fault_type,
        host=draft.host.strip(),
        severity=draft.severity,
        title=draft.title,
        summary=draft.summary,
        suggested_fix=draft.suggested_fix,
        evidence=evidence,
        runbook=RunbookRef(chunk_id=hit.chunk_id, source=hit.source, heading_path=hit.heading_path) if hit is not None else None,
        window_start=window.start,
        window_end=window.end,
    )


def _host_in_window(host: str, window: Window) -> bool:
    """The host wrote one of the lines, or a line names it as a whole word ("db-primary-01", not "db-primary-0")."""
    wanted = host.strip().lower()
    whole_word = re.compile(rf"(?<![\w.-]){re.escape(wanted)}(?![\w.-])")
    return any(event.host.lower() == wanted or whole_word.search(event.message.lower()) for event in window.events)


def _quote(raw: str) -> str:
    # quoted unchanged, unless longer than a ticket allows: then cut, and marked as cut
    return raw if len(raw) <= MAX_EVIDENCE_CHARS else raw[: MAX_EVIDENCE_CHARS - 1] + "…"
