import dataclasses

import pytest

from siteops_agent.log_context import LogContext
from siteops_agent.rag_client.base import RagError
from siteops_agent.rag_client.fake import FakeRagClient
from siteops_agent.ticket_sink.base import TicketSinkError
from siteops_agent.ticket_sink.memory import MemoryTicketSink
from siteops_agent.triage_tools import MAX_EVIDENCE, Dismissed, Ticketed, ToolFailure, ToolLimits, TriageTools

LIMITS = ToolLimits(max_searches=3, max_log_queries=3, hits_per_search=3)
KNOWN = frozenset({"db-replica-02", "db-primary-01", "api-01"})  # the hosts that wrote lines in the run

DRAFT = {
    "fault_type": "pg_primary_down",
    "host": "db-primary-01",  # the root cause, named in the replica's lines (window lines 2 to 4)
    "severity": "SEV-1",
    "title": "PostgreSQL primary db-primary-01 unreachable",
    "summary": "The replica cannot reach its primary and is not promoted: writes fail.",
    "suggested_fix": "Confirm the primary is down, then promote the replica.",
    "evidence_lines": [2, 3],
    "runbook_chunk_id": None,
}


@pytest.fixture
def rag(hits) -> FakeRagClient:
    return FakeRagClient(list(hits.values()))


@pytest.fixture
def sink() -> MemoryTicketSink:
    return MemoryTicketSink()


@pytest.fixture
def api_line(window):
    """An API host's line during the window: what log_context can bring in from another host."""
    first = window.events[1]
    return dataclasses.replace(first, host="api-01", component="public-api", message="POST /v2/orders 500 write failed", raw="api-01 POST 500")


@pytest.fixture
def tools(window, rag, sink, api_line) -> TriageTools:
    return TriageTools(window, rag, LogContext([*window.events, api_line]), sink, KNOWN, LIMITS)


# --- search_runbooks ---


def test_a_search_returns_passages_and_makes_them_citable(tools, rag) -> None:
    reply = tools.search_runbooks({"query": "primary down"})

    assert not reply.error
    assert "chunk_id=c-failover" in reply.text and "source=runbook_db_failover.docx" in reply.text
    assert "section=DB failover > Primary down" in reply.text
    assert set(tools.retrieved) == {"c-failover", "c-lag"}
    assert [(s.query, s.chunk_ids) for s in tools.searches] == [("primary down", ["c-failover", "c-lag"])]
    assert rag.requests[0].top_k == LIMITS.hits_per_search


def test_a_search_with_no_hits_says_so(window, sink) -> None:
    tools = TriageTools(window, FakeRagClient([]), LogContext(window.events), sink, KNOWN, LIMITS)

    assert tools.search_runbooks({"query": "printer"}).text == "No runbook passage matches."


def test_searches_stop_at_the_budget(tools, rag) -> None:
    for _ in range(LIMITS.max_searches):
        assert not tools.search_runbooks({"query": "q"}).error

    reply = tools.search_runbooks({"query": "one more"})

    assert reply.error and "budget used" in reply.text
    assert len(rag.requests) == LIMITS.max_searches  # rag is not asked again


@pytest.mark.parametrize("args", [{"query": ""}, {}, {"query": "q", "top_k": 50}], ids=["empty", "missing", "extra-field"])
def test_a_bad_search_is_refused_and_nothing_is_recorded(tools, rag, args) -> None:
    reply = tools.search_runbooks(args)

    assert reply.error and ("query" in reply.text or "top_k" in reply.text)  # names the field at fault
    assert tools.searches == [] and rag.requests == []


def test_rag_down_is_our_failure_not_the_models(tools, rag) -> None:
    rag.error = RagError("rag unreachable after 3 attempts")

    with pytest.raises(ToolFailure) as failure:
        tools.search_runbooks({"query": "q"})

    assert failure.value.service == "rag" and tools.searches == []


# --- log_context ---


def test_context_lines_are_numbered_after_the_window(tools, window, api_line) -> None:
    reply = tools.log_context({"host": "api-01", "before": 0, "after": 0})

    assert not reply.error
    assert f"  {len(window.events) + 1} | api-01 POST 500" in reply.text  # window lines are 1 to 4
    assert tools.shown.event(len(window.events) + 1) == api_line
    assert [(lookup.query, lookup.events) for lookup in tools.log_lookups] == [({"host": "api-01", "before": 0, "after": 0}, 1)]


def test_log_context_stops_at_the_budget(tools) -> None:
    for _ in range(LIMITS.max_log_queries):
        tools.log_context({"host": "api-01"})

    reply = tools.log_context({"host": "api-01"})

    assert reply.error and "budget used" in reply.text and len(tools.log_lookups) == LIMITS.max_log_queries


def test_without_a_log_budget_log_context_is_refused(window, rag, sink) -> None:
    tools = TriageTools(window, rag, LogContext(window.events), sink, KNOWN, dataclasses.replace(LIMITS, max_log_queries=0))

    assert tools.log_context({"host": "api-01"}).error


@pytest.mark.parametrize(
    ("args", "why"), [({"before": 99}, "before"), ({"pattern": "(unclosed"}, "invalid pattern")], ids=["out-of-bounds", "bad-regex"]
)
def test_a_bad_log_query_is_refused_with_a_reason(tools, args, why) -> None:
    reply = tools.log_context(args)

    assert reply.error and why in reply.text
    assert tools.log_lookups == [] and len(tools.shown) == len(tools.window.events)  # nothing shown, nothing counted


# --- create_ticket ---


def test_a_good_draft_is_checked_built_and_filed(tools, sink, window) -> None:
    reply = tools.create_ticket(DRAFT)

    assert not reply.error and reply.text == "Ticket T-1 opened. The window is done."
    assert isinstance(tools.decision, Ticketed) and tools.decision.result.created
    [ticket] = sink.tickets
    assert ticket.evidence == [window.events[1].raw, window.events[2].raw]  # the lines' text, filled by code
    assert (ticket.window_start, ticket.window_end) == (window.start, window.end)


def test_the_same_fault_again_is_reported_as_already_open(window, rag, sink) -> None:
    def fresh() -> TriageTools:
        return TriageTools(window, rag, LogContext(window.events), sink, KNOWN, LIMITS)

    fresh().create_ticket(DRAFT)
    second = fresh()

    reply = second.create_ticket(DRAFT)

    assert reply.text.startswith("Already open as T-1")
    assert isinstance(second.decision, Ticketed) and not second.decision.result.created
    assert len(sink.tickets) == 1


def test_too_many_evidence_lines_get_a_plain_reason_and_the_window_goes_on(tools, sink) -> None:
    # the probe's most frequent refusal: the model cited 22 lines of a 50-line window
    reply = tools.create_ticket(DRAFT | {"evidence_lines": list(range(1, MAX_EVIDENCE + 3))})

    assert reply.error and f"cite at most {MAX_EVIDENCE} lines, the clearest ones" in reply.text
    assert tools.decision is None and sink.tickets == []
    assert len(tools.check_errors) == 1


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"host": "orders-db"}, "is not a server in these logs"),
        ({"evidence_lines": [9]}, "do not exist"),
        ({"runbook_chunk_id": "c-failover"}, "was not returned by search_runbooks"),  # no search was made
    ],
    ids=["a-service-not-a-server", "a-line-that-doesnt-exist", "a-passage-never-retrieved"],
)
def test_a_draft_that_fails_its_checks_is_refused_and_can_be_fixed(tools, sink, change, why) -> None:
    refused = tools.create_ticket(DRAFT | change)

    assert refused.error and why in refused.text and "call create_ticket again" in refused.text
    assert tools.check_errors and sink.tickets == []
    assert not tools.create_ticket(DRAFT).error  # the corrected draft goes through


def test_a_retrieved_passage_can_be_cited(tools, sink) -> None:
    tools.search_runbooks({"query": "primary down"})

    tools.create_ticket(DRAFT | {"runbook_chunk_id": "c-failover"})

    assert sink.tickets[0].runbook is not None and sink.tickets[0].runbook.source == "runbook_db_failover.docx"


def test_lines_brought_in_by_log_context_can_be_evidence(tools, sink, window) -> None:
    tools.log_context({"host": "api-01", "before": 0, "after": 0})

    tools.create_ticket(DRAFT | {"evidence_lines": [3, len(window.events) + 1]})

    assert sink.tickets[0].evidence == [window.events[2].raw, "api-01 POST 500"]


def test_tickets_down_is_our_failure_and_nothing_is_decided(window, rag) -> None:
    class DownSink(MemoryTicketSink):
        def create(self, ticket):  # type: ignore[no-untyped-def]
            raise TicketSinkError("tickets service unreachable after 3 attempts")

    tools = TriageTools(window, rag, LogContext(window.events), DownSink(), KNOWN, LIMITS)

    with pytest.raises(ToolFailure) as failure:
        tools.create_ticket(DRAFT)

    assert failure.value.service == "tickets" and tools.decision is None


# --- dismiss, and after the decision ---


def test_dismiss_closes_the_window(tools) -> None:
    reply = tools.dismiss({"reason": "one 504, then successes", "looks_like": "high_latency"})

    assert reply.text == "Dismissed. The window is done."
    assert isinstance(tools.decision, Dismissed) and tools.decision.dismissal.looks_like == "high_latency"


def test_a_dismissal_needs_a_reason(tools) -> None:
    assert tools.dismiss({"reason": ""}).error and tools.decision is None


def test_once_decided_every_tool_refuses(tools, rag, sink) -> None:
    tools.dismiss({"reason": "transient"})

    replies = [tools.search_runbooks({"query": "q"}), tools.log_context({}), tools.create_ticket(DRAFT), tools.dismiss({"reason": "again"})]

    assert all(reply.error and "already decided" in reply.text for reply in replies)
    assert rag.requests == [] and sink.tickets == []
    assert isinstance(tools.decision, Dismissed) and tools.decision.dismissal.reason == "transient"  # the first decision stands


@pytest.mark.parametrize("fields", [{"max_searches": 0}, {"hits_per_search": 0}, {"max_log_queries": -1}])
def test_limits_need_at_least_one_search_returning_a_hit(fields) -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(LIMITS, **fields)
