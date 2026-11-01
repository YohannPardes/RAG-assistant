from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from siteops_agent.decision import Dismissal, TicketDraft
from siteops_agent.report import Search, TriageReport, WindowOutcome

SINCE = datetime(2026, 11, 1, 6, tzinfo=UTC)
DRAFT = TicketDraft(
    fault_type="pg_primary_down",
    host="db-primary-01",
    severity="SEV-1",
    title="Primary down",
    summary="The replica cannot reach its primary.",
    suggested_fix="Promote the replica.",
    evidence_lines=[2],
)


def report(*outcomes: WindowOutcome) -> TriageReport:
    return TriageReport(
        run_id="r1", since=SINCE, until=SINCE + timedelta(hours=4), model="qwen3.5:27b", events_read=100, unparsable=0, outcomes=list(outcomes)
    )


def test_an_outcome_carries_its_windows_identity_and_span(window) -> None:
    outcome = WindowOutcome.of(window, "dismissed", dismissal=Dismissal(reason="transient"))

    assert (outcome.window_id, outcome.host, outcome.rule) == (window.id, "db-replica-02", "db_leader_unreachable")
    assert (outcome.start, outcome.end, outcome.events, outcome.omitted) == (window.start, window.end, 4, 0)


@pytest.mark.parametrize(
    ("status", "details"),
    [
        ("ticketed", {"draft": DRAFT, "ticket_id": "T-1"}),
        ("deduplicated", {"draft": DRAFT, "ticket_id": "T-1"}),
        ("dismissed", {"dismissal": Dismissal(reason="transient")}),
        ("unresolved", {}),
        ("error", {"error": "rag: connection refused"}),
    ],
)
def test_each_status_with_its_details_is_valid(window, status, details) -> None:
    assert WindowOutcome.of(window, status, **details).status == status


@pytest.mark.parametrize(
    ("status", "details"),
    [("ticketed", {"draft": DRAFT}), ("deduplicated", {"ticket_id": "T-1"}), ("dismissed", {}), ("error", {})],
    ids=["ticketed-without-id", "deduplicated-without-draft", "dismissed-without-reason", "error-without-message"],
)
def test_a_status_without_its_details_is_refused(window, status, details) -> None:
    # the eval reads draft.fault_type and draft.host of every ticket: they can't be missing
    with pytest.raises(ValidationError, match="without its details"):
        WindowOutcome.of(window, status, **details)


def test_totals_count_every_status_including_zeros(window) -> None:
    dismissed = WindowOutcome.of(window, "dismissed", dismissal=Dismissal(reason="transient"))
    ticketed = WindowOutcome.of(window, "ticketed", draft=DRAFT, ticket_id="T-1")

    assert report(dismissed, dismissed, ticketed).totals == {"ticketed": 1, "deduplicated": 0, "dismissed": 2, "unresolved": 0, "error": 0}


def test_a_report_survives_a_trip_through_json(window) -> None:
    # written by --report, read back by the eval
    searched = WindowOutcome.of(
        window,
        "ticketed",
        draft=DRAFT,
        ticket_id="T-1",
        searches=[Search(query="primary down", chunk_ids=["c-failover"], sources=["runbook_db_failover.docx"])],
        check_errors=["host 'x' does not appear in the window's lines"],
        llm_calls=3,
        duration_s=9.5,
    )
    original = report(searched)

    text = original.model_dump_json()

    assert '"totals"' in text  # in the file, so a reader sees it first
    assert TriageReport.model_validate_json(text) == original


def test_a_reversed_range_is_refused() -> None:
    with pytest.raises(ValidationError, match="before since"):
        TriageReport(run_id="r1", since=SINCE, until=SINCE - timedelta(seconds=1), model="m", events_read=0, unparsable=0, outcomes=[])
