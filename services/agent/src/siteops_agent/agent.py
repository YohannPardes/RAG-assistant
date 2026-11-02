"""The triage agent (agent/4): one window at a time, the model and its tools in a loop until it decides.

    diagnose ── tool call ──────────────► tools ── decided ──► END  (ticketed / deduplicated / dismissed)
       ▲                                   │
       └──────────── not decided yet ◄─────┘
    diagnose ── plain text, first time ──► diagnose (one reminder: answer with a tool call)
    diagnose ── plain text again, or the model-call budget used up ──► END  (unresolved)

The graph is LangGraph's; what each step does is ours: the tools are TriageTools (triage_tools.py, CORE), handed to
the model by tools.py. A ToolFailure (rag or tickets down) or a failing model call ends the window as "error".
`run_triage` (read logs → windows → TriageAgent.triage for each → TriageReport) is plain Python and doesn't import this.
"""

import json
import logging
import time
from collections.abc import Mapping, Sequence, Set
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from siteops_agent.log_context import LogContext
from siteops_agent.models import Window
from siteops_agent.prompts import SYSTEM_PROMPT, render_window
from siteops_agent.rag_client.base import RagClient
from siteops_agent.report import Status, WindowOutcome
from siteops_agent.ticket_sink.base import TicketSink
from siteops_agent.tools import as_tools
from siteops_agent.triage_tools import Dismissed, Ticketed, ToolFailure, ToolLimits, TriageTools

logger = logging.getLogger(__name__)

# Sent once, when the model answers in plain text instead of calling a tool.
REMINDER = "Answer with a tool call: create_ticket or dismiss to decide, or search_runbooks / log_context to look further."


class WindowState(TypedDict):
    """What flows between the graph's nodes for one window. The rest of the window's memory is in its TriageTools."""

    messages: Annotated[list[BaseMessage], add_messages]  # the instructions, the window, every reply and tool result
    llm_calls: int  # model calls so far: past the budget, the window ends unresolved
    reminded: bool  # True once the model was told to answer with a tool call


Route = Literal["tools", "diagnose", "end"]


class TriageAgent:
    """Investigates windows one at a time with the model and the triage tools. Built once per run by the container."""

    def __init__(self, model: BaseChatModel, rag: RagClient, sink: TicketSink, limits: ToolLimits, max_llm_calls: int) -> None:
        """What every window of a run shares: the model, the clients, the budgets. Each window gets its own TriageTools."""
        if max_llm_calls < 1:
            raise ValueError("a window needs at least one model call")
        self.model = model  # configured by the container: temperature 0, a seed, thinking off, a timeout (arch/7)
        self._rag = rag
        self._sink = sink
        self.limits = limits  # tool budgets per window: searches, log_context calls, hits per search
        self.max_llm_calls = max_llm_calls  # model calls per window: past it, the window ends unresolved

    def triage(self, window: Window, context: LogContext, known_hosts: Set[str]) -> WindowOutcome:
        """One investigation: fresh TriageTools for the window, the graph run until it decides, its outcome.
        Never raises for a window: our failures and the model's become an "error" outcome."""

        started = time.monotonic()

        # create the graph's nodes and edges + connect tools to the model.
        tools = TriageTools(window, self._rag, context, self._sink, known_hosts, self.limits)
        graph = self._graph(tools)

        # setting the first state: the instructions and the window.
        state: WindowState = {
            "messages": [SystemMessage(SYSTEM_PROMPT), HumanMessage(render_window(window))],
            "llm_calls": 0,
            "reminded": False,
        }

        # run the graph until it decides or the model fails (the window ends as "error").
        # Each model call is at most two steps (diagnose, then tools): the routes end the window before this limit does.
        # stream yields the full state after each step: the last one is kept even when a step fails, for the log.
        final = state
        try:
            for step in graph.stream(state, config={"recursion_limit": 2 * self.max_llm_calls + 2}, stream_mode="values"):
                final = cast(WindowState, step)
        except ToolFailure as failure:  # rag or tickets down
            outcome = self._error(window, tools, final, str(failure), time.monotonic() - started)
        except Exception as failure:  # the model call: a timeout, a refused connection, a reply LangChain can't read
            outcome = self._error(window, tools, final, f"model: {type(failure).__name__}: {failure}", time.monotonic() - started)
        else:
            outcome = self._outcome(window, tools, final, time.monotonic() - started)
        logger.info("%s", transcript(outcome, final["messages"], as_tools(tools)))
        return outcome

    # --- the graph, built per window (its tools are bound to that window's TriageTools) ---

    def _graph(self, tools: TriageTools) -> CompiledStateGraph[WindowState, None, WindowState, WindowState]:
        """diagnose <-> tools, compiled with the routes below."""
        lc_tools = as_tools(tools)
        by_name = {tool.name: tool for tool in lc_tools}
        model = self.model.bind_tools(lc_tools)  # the model reads each tool's name, description and schema

        graph = StateGraph(WindowState)
        graph.add_node("diagnose", lambda state: self._diagnose(state, model))
        graph.add_node("tools", lambda state: self._run_tools(state, by_name))
        graph.add_edge(START, "diagnose")
        graph.add_conditional_edges(
            "diagnose",
            lambda state: after_diagnose(state, self.max_llm_calls),
            {"tools": "tools", "diagnose": "diagnose", "end": END},
        )
        graph.add_conditional_edges(
            "tools",
            lambda state: after_tools(tools, state, self.max_llm_calls),
            {"diagnose": "diagnose", "end": END},
        )
        return graph.compile()

    def _diagnose(self, state: WindowState, model: Runnable[LanguageModelInput, AIMessage]) -> dict[str, object]:
        """Node: call the model with the messages so far and the window's tools bound; count the call.
        Back here after a plain-text reply (the last message is the model's own): remind it first, once."""
        last = state["messages"][-1]
        reminder = [HumanMessage(REMINDER)] if isinstance(last, AIMessage) else []
        reply = model.invoke([*state["messages"], *reminder])
        return {
            "messages": [*reminder, reply],
            "llm_calls": state["llm_calls"] + 1,
            "reminded": state["reminded"] or bool(reminder),
        }

    def _run_tools(self, state: WindowState, by_name: Mapping[str, BaseTool]) -> dict[str, object]:
        """Node: run the tool calls of the last reply; append their results.
        Every call gets a reply, in order: an unknown tool or unreadable arguments is refused, never skipped.
        A ToolFailure is not caught: it ends the window as "error" (see triage)."""
        reply = state["messages"][-1]
        assert isinstance(reply, AIMessage), "the tools node runs after a reply of the model"
        results: list[BaseMessage] = []
        for call in reply.tool_calls:
            tool = by_name.get(call["name"])
            if tool is None:
                known = ", ".join(by_name)
                results.append(_refused(call["id"], f"there is no tool {call['name']!r}: use one of {known}"))
            else:
                results.append(tool.invoke(call))  # a ToolCall in → a ToolMessage out, status "error" when TriageTools refused
        for bad in reply.invalid_tool_calls:  # arguments that weren't valid JSON
            results.append(_refused(bad["id"], f"the arguments of {bad['name']!r} could not be read ({bad['error']}): call it again with valid JSON"))
        return {"messages": results}

    # --- after the investigation ---

    def _outcome(self, window: Window, tools: TriageTools, state: WindowState, seconds: float) -> WindowOutcome:
        """The report's line for the window, from its decision (ticketed / deduplicated / dismissed) or its absence (unresolved)."""
        trace = _trace(tools, state, seconds)
        match tools.decision:
            case Ticketed(draft, result):
                status: Status = "ticketed" if result.created else "deduplicated"
                return WindowOutcome.of(window, status, draft=draft, ticket_id=result.ticket.id, **trace)
            case Dismissed(dismissal):
                return WindowOutcome.of(window, "dismissed", dismissal=dismissal, **trace)
            case None:  # the model stopped without deciding, or the model-call budget was used up
                return WindowOutcome.of(window, "unresolved", **trace)

    def _error(self, window: Window, tools: TriageTools, state: WindowState, message: str, seconds: float) -> WindowOutcome:
        """The report's line for a window whose investigation failed: what failed, and what was done before it
        (`state` is the last one before the failure)."""
        return WindowOutcome.of(window, "error", error=message, **_trace(tools, state, seconds))


def _trace(tools: TriageTools, state: WindowState, seconds: float) -> dict[str, Any]:
    """How the investigation went, whatever its end: for debugging a window and for the eval."""
    return {
        "searches": tools.searches,
        "log_lookups": tools.log_lookups,
        "check_errors": tools.check_errors,
        "llm_calls": state["llm_calls"],
        "duration_s": seconds,
    }


def _refused(call_id: str | None, why: str) -> ToolMessage:
    """A tool call the loop itself refuses, as the model reads a refusal from TriageTools."""
    return ToolMessage(f"Call refused: {why}.", tool_call_id=call_id or "", status="error")


# --- the run's log: one entry per window, nothing truncated ---


def transcript(outcome: WindowOutcome, messages: Sequence[BaseMessage], tools: Sequence[BaseTool]) -> str:
    """Everything the model was given and everything it said, in full: the tools as sent (name, description,
    schema), the instructions, the window, every reply, tool call and tool result, then the outcome."""
    parts = [f"===== window {outcome.window_id} on {outcome.host} ({outcome.rule}): {outcome.status}, {outcome.llm_calls} model calls ====="]
    parts += ["----- tools offered -----", *(json.dumps(convert_to_openai_tool(tool), indent=2) for tool in tools)]
    for message in messages:
        match message:
            case ToolMessage():
                parts.append(f"----- tool {message.name or ''} [{message.tool_call_id}] {message.status} -----")
            case _:
                parts.append(f"----- {message.type} -----")
        parts.append(message.text)  # the whole content, as text
        if isinstance(message, AIMessage):
            parts += [f"tool call {call['name']} [{call['id']}]: {json.dumps(call['args'])}" for call in message.tool_calls]
            parts += [f"invalid tool call {bad['name']} [{bad['id']}]: {bad['args']} ({bad['error']})" for bad in message.invalid_tool_calls]
    parts += ["----- outcome -----", outcome.model_dump_json(indent=2)]
    return "\n".join(parts)


# --- routing: small pure functions of the state, testable without a model ---


def after_diagnose(state: WindowState, max_llm_calls: int) -> Route:
    """A tool call → "tools"; plain text → "diagnose" once (a reminder), then "end"; the budget used up → "end"."""
    reply = state["messages"][-1]
    assert isinstance(reply, AIMessage), "diagnose ends with a reply of the model"
    if reply.tool_calls or reply.invalid_tool_calls:  # first: a decision on the last allowed call still counts
        return "tools"  # unreadable arguments too: the tools node tells the model why
    if not state["reminded"] and state["llm_calls"] < max_llm_calls:
        return "diagnose"
    return "end"  # unresolved


def after_tools(tools: TriageTools, state: WindowState, max_llm_calls: int) -> Route:
    """Decided (a ticket filed or the window dismissed) → "end"; the model-call budget used up → "end" (unresolved);
    otherwise back to "diagnose". The budget is checked here because diagnose is where the model is called."""
    if tools.decision is not None:
        return "end"
    if state["llm_calls"] >= max_llm_calls:
        return "end"
    return "diagnose"
