import logging
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from siteops_agent.agent import REMINDER, TriageAgent, WindowState, after_diagnose, after_tools
from siteops_agent.log_context import LogContext
from siteops_agent.prompts import SYSTEM_PROMPT, render_window
from siteops_agent.rag_client.base import RagError
from siteops_agent.rag_client.fake import FakeRagClient
from siteops_agent.ticket_sink.memory import MemoryTicketSink
from siteops_agent.tools import CREATE_TICKET
from siteops_agent.triage_tools import Dismissed, ToolLimits, TriageTools

LIMITS = ToolLimits(max_searches=3, max_log_queries=3, hits_per_search=3)
KNOWN = frozenset({"db-replica-02", "db-primary-01"})
MAX_LLM_CALLS = 4

DRAFT = {
    "fault_type": "pg_primary_down",
    "host": "db-primary-01",
    "severity": "SEV-1",
    "title": "PostgreSQL primary db-primary-01 unreachable",
    "summary": "The replica cannot reach its primary and is not promoted: writes fail.",
    "suggested_fix": "Confirm the primary is down, then promote the replica.",
    "evidence_lines": [2, 3],
    "runbook_chunk_id": "c-failover",
}


class ScriptedModel(BaseChatModel):
    """Answers with the replies it was given, in order, and records what it was sent. bind_tools: itself."""

    replies: list[Any]  # AIMessage, or an Exception to raise
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":  # type: ignore[override]
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])


def call(name: str, args: dict[str, Any], id: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id, "type": "tool_call"}])


def text(content: str) -> AIMessage:
    return AIMessage(content=content)


@pytest.fixture
def rag(hits) -> FakeRagClient:
    return FakeRagClient(list(hits.values()))


@pytest.fixture
def sink() -> MemoryTicketSink:
    return MemoryTicketSink()


@pytest.fixture
def run(window, rag, sink):
    """triage() on the window with a model that answers with `replies`; returns the outcome and the model."""

    def run(*replies: AIMessage | Exception, max_llm_calls: int = MAX_LLM_CALLS) -> tuple[Any, ScriptedModel]:
        model = ScriptedModel(replies=list(replies))
        outcome = TriageAgent(model, rag, sink, LIMITS, max_llm_calls).triage(window, LogContext(window.events), KNOWN)
        return outcome, model

    return run


def tool_replies(model: ScriptedModel) -> list[ToolMessage]:
    """The tool results in the last request to the model."""
    return [message for message in model.seen[-1] if isinstance(message, ToolMessage)]


# --- routing ---

UNREADABLE = AIMessage(content="", invalid_tool_calls=[{"name": "dismiss", "args": "{", "id": "c1", "error": "bad", "type": "invalid_tool_call"}])


def state(reply: AIMessage, llm_calls: int = 1, reminded: bool = False) -> WindowState:
    return {"messages": [reply], "llm_calls": llm_calls, "reminded": reminded}


@pytest.mark.parametrize(
    ("reply", "llm_calls", "reminded", "route"),
    [
        (call("search_runbooks", {"query": "q"}), 1, False, "tools"),
        (call("create_ticket", DRAFT), MAX_LLM_CALLS, False, "tools"),  # a decision on the last call still counts
        (UNREADABLE, 1, False, "tools"),  # the tools node tells the model why
        (text("I think the primary is down."), 1, False, "diagnose"),  # one reminder
        (text("Still thinking."), 2, True, "end"),  # reminded already: unresolved
        (text("I think the primary is down."), MAX_LLM_CALLS, False, "end"),  # no model call left for a reminder
    ],
)
def test_after_diagnose(reply, llm_calls, reminded, route) -> None:
    assert after_diagnose(state(reply, llm_calls, reminded), MAX_LLM_CALLS) == route


def test_after_tools_ends_once_decided_even_with_budget_left(window, rag, sink) -> None:
    tools = TriageTools(window, rag, LogContext(window.events), sink, KNOWN, LIMITS)
    tools.dismiss({"reason": "transient"})

    assert isinstance(tools.decision, Dismissed)
    assert after_tools(tools, state(text(""), llm_calls=1), MAX_LLM_CALLS) == "end"


def test_after_tools_goes_back_to_the_model_until_the_budget_is_used(window, rag, sink) -> None:
    tools = TriageTools(window, rag, LogContext(window.events), sink, KNOWN, LIMITS)

    assert after_tools(tools, state(text(""), llm_calls=MAX_LLM_CALLS - 1), MAX_LLM_CALLS) == "diagnose"
    assert after_tools(tools, state(text(""), llm_calls=MAX_LLM_CALLS), MAX_LLM_CALLS) == "end"


# --- a window, start to end ---


def test_the_model_starts_from_the_instructions_and_the_window(run, window) -> None:
    _, model = run(call("dismiss", {"reason": "transient"}))

    [first] = model.seen
    assert [type(message) for message in first] == [type(first[0]), HumanMessage]
    assert first[0].content == SYSTEM_PROMPT and first[1].content == render_window(window)


def test_a_search_then_a_ticket(run, sink) -> None:
    outcome, model = run(call("search_runbooks", {"query": "replica cannot reach primary"}), call("create_ticket", DRAFT, id="c2"))

    assert outcome.status == "ticketed" and outcome.ticket_id == "T-1" and outcome.draft.host == "db-primary-01"
    assert [search.query for search in outcome.searches] == ["replica cannot reach primary"]
    assert outcome.llm_calls == 2 and len(sink.tickets) == 1
    [passages] = tool_replies(model)
    assert passages.tool_call_id == "c1" and "chunk_id=c-failover" in passages.text  # the model read the search result


def test_the_same_fault_again_is_deduplicated(run, sink) -> None:
    run(call("search_runbooks", {"query": "q"}), call("create_ticket", DRAFT))
    outcome, _ = run(call("search_runbooks", {"query": "q"}), call("create_ticket", DRAFT))

    assert outcome.status == "deduplicated" and outcome.ticket_id == "T-1" and len(sink.tickets) == 1


def test_a_dismissal(run, sink) -> None:
    outcome, _ = run(call("dismiss", {"reason": "one refused connection, then recovered"}))

    assert outcome.status == "dismissed" and outcome.dismissal.reason.startswith("one refused")
    assert outcome.llm_calls == 1 and sink.tickets == []


def test_a_refused_ticket_is_explained_and_the_fixed_one_is_filed(run, sink) -> None:
    wrong = {**DRAFT, "evidence_lines": [9]}  # the window has 4 lines

    outcome, model = run(call("search_runbooks", {"query": "q"}), call("create_ticket", wrong, id="c2"), call("create_ticket", DRAFT, id="c3"))

    assert outcome.status == "ticketed" and len(outcome.check_errors) == 1 and len(sink.tickets) == 1
    refusal = tool_replies(model)[-1]
    assert refusal.status == "error" and refusal.text.startswith("Ticket refused") and "9" in refusal.text


def test_plain_text_gets_one_reminder_then_the_window_is_unresolved(run, sink) -> None:
    outcome, model = run(text("The primary looks down."), text("Yes, it is down."))

    assert outcome.status == "unresolved" and outcome.llm_calls == 2 and sink.tickets == []
    reminder = model.seen[1][-1]  # the second call ends with the reminder
    assert isinstance(reminder, HumanMessage) and reminder.content == REMINDER


def test_a_reminder_is_enough_to_get_a_decision(run) -> None:
    outcome, _ = run(text("The primary looks down."), call("dismiss", {"reason": "transient"}))

    assert outcome.status == "dismissed" and outcome.llm_calls == 2


def test_the_model_call_budget_ends_the_window_unresolved(run, sink) -> None:
    searches = [call("search_runbooks", {"query": f"q{i}"}, id=f"c{i}") for i in range(10)]

    outcome, model = run(*searches, max_llm_calls=2)

    assert outcome.status == "unresolved" and outcome.llm_calls == 2 and len(model.seen) == 2 and sink.tickets == []


def test_an_unknown_tool_and_unreadable_arguments_are_refused_and_the_window_goes_on(run) -> None:
    bad = AIMessage(
        content="",
        tool_calls=[{"name": "restart_server", "args": {}, "id": "c1", "type": "tool_call"}],
        invalid_tool_calls=[{"name": "dismiss", "args": "{reason:", "id": "c2", "error": "Expecting value", "type": "invalid_tool_call"}],
    )

    outcome, model = run(bad, call("dismiss", {"reason": "transient"}, id="c3"))

    assert outcome.status == "dismissed"
    unknown, unreadable = tool_replies(model)
    assert (unknown.tool_call_id, unknown.status) == ("c1", "error") and "no tool 'restart_server'" in unknown.text
    assert (unreadable.tool_call_id, unreadable.status) == ("c2", "error") and "could not be read" in unreadable.text


def test_calls_after_the_decision_in_the_same_reply_are_refused(run, sink) -> None:
    both = AIMessage(
        content="",
        tool_calls=[
            {"name": "dismiss", "args": {"reason": "transient"}, "id": "c1", "type": "tool_call"},
            {"name": "create_ticket", "args": DRAFT, "id": "c2", "type": "tool_call"},
        ],
    )

    outcome, _ = run(both)

    assert outcome.status == "dismissed" and sink.tickets == [] and outcome.llm_calls == 1


def test_rag_down_ends_the_window_as_an_error_without_asking_the_model_again(run, rag) -> None:
    rag.error = RagError("rag unreachable after 3 attempts")

    outcome, model = run(call("search_runbooks", {"query": "q"}), call("dismiss", {"reason": "x"}))

    assert outcome.status == "error" and outcome.error.startswith("rag:")
    assert outcome.llm_calls == 1 and len(model.seen) == 1  # the failure was never sent to the model


def test_a_failing_model_call_ends_the_window_as_an_error(run) -> None:
    outcome, _ = run(call("search_runbooks", {"query": "q"}), TimeoutError("no reply in 60s"))

    assert outcome.status == "error" and outcome.error == "model: TimeoutError: no reply in 60s"
    assert outcome.llm_calls == 1 and [search.query for search in outcome.searches] == ["q"]  # what was done before


def test_log_context_calls_are_in_the_outcome(run) -> None:
    outcome, _ = run(call("log_context", {"host": "db-replica-02", "before": 0, "after": 0}), call("dismiss", {"reason": "x"}, id="c2"))

    assert [lookup.query for lookup in outcome.log_lookups] == [{"host": "db-replica-02", "before": 0, "after": 0}]


# --- the run's log ---


def test_each_window_is_logged_in_full(run, window, caplog) -> None:
    long_reason = "transient " * 90  # 900 characters, under the field's limit
    with caplog.at_level(logging.INFO, logger="siteops_agent.agent"):
        outcome, _ = run(call("search_runbooks", {"query": "q"}), call("dismiss", {"reason": long_reason}, id="c2"))

    [record] = caplog.records
    log = record.getMessage()
    assert f"window {outcome.window_id}" in log and "dismissed" in log
    assert SYSTEM_PROMPT in log and render_window(window) in log  # the starting prompt, whole
    assert CREATE_TICKET in log and '"evidence_lines"' in log  # the tools as sent: descriptions and schemas
    assert 'tool call search_runbooks [c1]: {"query": "q"}' in log
    assert "chunk_id=c-failover" in log  # the tool's result
    assert long_reason.strip() in log  # nothing truncated


def test_a_failed_window_is_logged_with_what_happened_before(run, rag, caplog) -> None:
    rag.error = RagError("rag unreachable")
    with caplog.at_level(logging.INFO, logger="siteops_agent.agent"):
        run(call("search_runbooks", {"query": "replica cannot reach primary"}))

    [record] = caplog.records
    log = record.getMessage()
    assert "error" in log and "rag unreachable" in log
    assert "tool call search_runbooks [c1]" in log  # the call that failed is there
