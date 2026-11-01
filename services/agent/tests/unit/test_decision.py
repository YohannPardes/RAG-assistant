import dataclasses

import pytest
from pydantic import ValidationError

from siteops_agent.decision import MAX_EVIDENCE_CHARS, Dismissal, TicketDraft, check_draft, to_ticket_create
from siteops_common.contracts import RunbookRef

DRAFT = {
    "fault_type": "pg_primary_down",
    "host": "db-primary-01",  # the root cause, only named in the replica's messages
    "severity": "SEV-1",
    "title": "PostgreSQL primary db-primary-01 unreachable",
    "summary": "The replica cannot reach its primary and is not promoted: writes fail.",
    "suggested_fix": "Follow the failover runbook: confirm the primary is down, then promote the replica.",
    "evidence_lines": [2, 3],
    "runbook_chunk_id": "c-failover",
}


def draft(**changes) -> TicketDraft:
    return TicketDraft.model_validate(DRAFT | changes)


# --- check_draft ---


def test_a_good_draft_passes(window, hits) -> None:
    assert check_draft(draft(), window, hits) == []


@pytest.mark.parametrize("host", ["db-replica-02", "db-primary-01", "  DB-Primary-01 "], ids=["writer", "named-in-a-message", "case-and-spaces"])
def test_the_host_may_be_the_writer_or_named_in_a_line(window, hits, host) -> None:
    assert check_draft(draft(host=host), window, hits) == []


@pytest.mark.parametrize("host", ["api-01", "db-primary-0", "primary"], ids=["absent", "prefix-of-a-host", "part-of-a-word"])
def test_a_host_the_window_does_not_name_is_rejected(window, hits, host) -> None:
    # an injected line can't make the agent file a ticket against an unrelated host (agent/10)
    [problem] = check_draft(draft(host=host), window, hits)

    assert "does not appear" in problem


@pytest.mark.parametrize("numbers", [[0], [5], [1, 99]])
def test_evidence_lines_must_exist(window, hits, numbers) -> None:
    [problem] = check_draft(draft(evidence_lines=numbers), window, hits)

    assert "do not exist" in problem and "1 to 4" in problem


def test_the_runbook_must_come_from_a_search_of_this_window(window, hits) -> None:
    [problem] = check_draft(draft(runbook_chunk_id="c-invented"), window, hits)

    assert "not returned by search_runbooks" in problem
    assert check_draft(draft(runbook_chunk_id=None), window, hits) == []  # no runbook fits: allowed


def test_every_problem_is_reported_at_once(window, hits) -> None:
    # the model fixes them in one retry, not one per call
    problems = check_draft(draft(host="api-01", evidence_lines=[9], runbook_chunk_id="c-invented"), window, hits)

    assert len(problems) == 3


# --- to_ticket_create ---


def test_code_fills_the_facts(window, hits) -> None:
    ticket = to_ticket_create(draft(evidence_lines=[3, 2, 3]), window, hits)

    assert ticket.evidence == [window.events[2].raw, window.events[1].raw]  # the lines' text, in the model's order, once each
    assert (ticket.window_start, ticket.window_end) == (window.start, window.end)
    assert ticket.runbook == RunbookRef(chunk_id="c-failover", source="runbook_db_failover.docx", heading_path=["DB failover", "Primary down"])
    assert (ticket.fault_type, ticket.host, ticket.severity) == ("pg_primary_down", "db-primary-01", "SEV-1")


def test_the_host_is_stored_without_surrounding_spaces(window, hits) -> None:
    assert to_ticket_create(draft(host="  db-primary-01 "), window, hits).host == "db-primary-01"


def test_no_runbook_gives_a_ticket_without_one(window, hits) -> None:
    assert to_ticket_create(draft(runbook_chunk_id=None), window, hits).runbook is None


def test_a_draft_that_fails_its_checks_never_becomes_a_ticket(window, hits) -> None:
    with pytest.raises(ValueError, match="fails its checks"):
        to_ticket_create(draft(host="api-01"), window, hits)


def test_an_evidence_line_longer_than_a_ticket_allows_is_cut_and_marked(window, hits) -> None:
    long = dataclasses.replace(window.events[1], raw="x" * (MAX_EVIDENCE_CHARS + 500))
    wide = dataclasses.replace(window, events=(window.events[0], long, *window.events[2:]))

    [evidence] = to_ticket_create(draft(evidence_lines=[2]), wide, hits).evidence

    assert len(evidence) == MAX_EVIDENCE_CHARS and evidence.endswith("…")


def test_a_draft_at_its_bounds_is_a_valid_ticket(window, hits) -> None:
    # TicketDraft's bounds are TicketCreate's: a draft that validates never fails to become a ticket
    widest = draft(title="t" * 200, summary="s" * 4000, suggested_fix="f" * 4000, evidence_lines=[1, 2, 3, 4] * 5)

    assert to_ticket_create(widest, window, hits).title == "t" * 200


# --- the models' own bounds ---


@pytest.mark.parametrize(
    "change",
    [
        {"fault_type": "overheating"},
        {"severity": "SEV-0"},
        {"evidence_lines": []},
        {"evidence_lines": list(range(1, 22))},
        {"title": ""},
        {"window_start": "x"},
    ],
    ids=["unknown-fault-type", "unknown-severity", "no-evidence", "too-much-evidence", "empty-title", "a-fact-it-may-not-write"],
)
def test_an_invalid_draft_is_refused(change) -> None:
    with pytest.raises(ValidationError):
        draft(**change)


def test_a_dismissal_needs_a_reason() -> None:
    assert Dismissal(reason="one 504, then successes").looks_like is None
    with pytest.raises(ValidationError):
        Dismissal(reason="")
