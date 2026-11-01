"""One window's tools as the model sees them: LangChain tools over TriageTools (agent/4). Plumbing and descriptions only.

What the model reads is here: each tool's name, its description (prompt text) and its argument schema. What happens
is in triage_tools.py. A tool's schema is given to LangChain as JSON, so LangChain doesn't validate the arguments
itself: the raw arguments reach TriageTools, which validates them and refuses in words the model can act on.

  ToolReply            → the text the model reads
  ToolReply(error=True) → ToolException: the same text, as a tool message marked as an error (the call was refused)
  ToolFailure          → not caught here: it reaches the loop, which ends the window as "error"
"""

from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel

from siteops_agent.decision import Dismissal, TicketDraft
from siteops_agent.log_context import LogQuery
from siteops_agent.triage_tools import RunbookQuery, ToolReply, TriageTools

SEARCH_RUNBOOKS = (
    "Search the runbooks for passages whose symptoms match what the window shows. Returns a few passages, each with "
    "its chunk_id (what you cite as runbook_chunk_id), its source and its section. Search for what the logs show, "
    "in a few words; a later search can refine it."
)
LOG_CONTEXT = (
    "More log lines around the window, from any host, filtered by host, component, minimum level and a regular "
    "expression on the message. Use it only when the window doesn't show the cause, e.g. its errors point at another "
    "server; don't use it to re-read a window that is already clear. The lines it returns are numbered after the ones "
    "you have, and can be cited as evidence."
)
CREATE_TICKET = (
    "Open an incident ticket for a confirmed fault that needs human action, and end the window. Give your judgement: "
    "the fault type, the server at fault (it must have written lines in these logs), the severity, a title, a summary, "
    "the fix, the numbers of the lines that prove it, and the chunk_id of the runbook passage it follows. The code "
    "checks every claim against what you were shown; if it refuses, read why and call again."
)
DISMISS = (
    "End the window without a ticket: its events are transient or harmless (a single slow request, one timeout "
    "followed by successes, a check that failed once and recovered). Say why."
)


def as_tools(tools: TriageTools) -> list[BaseTool]:
    """The tools of one window, bound to its TriageTools."""
    return [search_runbooks(tools), log_context(tools), create_ticket(tools), dismiss(tools)]


def search_runbooks(tools: TriageTools) -> BaseTool:
    return _tool("search_runbooks", SEARCH_RUNBOOKS, RunbookQuery, tools.search_runbooks)


def log_context(tools: TriageTools) -> BaseTool:
    """More log lines around the window, from any host (agent/14)."""
    return _tool("log_context", LOG_CONTEXT, LogQuery, tools.log_context)


def create_ticket(tools: TriageTools) -> BaseTool:
    """Open a ticket from the model's judgement; the code checks it and fills the facts (agent/10)."""
    return _tool("create_ticket", CREATE_TICKET, TicketDraft, tools.create_ticket)


def dismiss(tools: TriageTools) -> BaseTool:
    """Close the window without a ticket."""
    return _tool("dismiss", DISMISS, Dismissal, tools.dismiss)


def _tool(name: str, description: str, schema: type[BaseModel], method: Callable[[Mapping[str, Any]], ToolReply]) -> BaseTool:
    """A LangChain tool that hands the model's raw arguments to `method` and returns its reply."""

    def run(**args: Any) -> str:
        reply = method(args)
        if reply.error:
            raise ToolException(reply.text)  # handle_tool_error: the model reads it, marked as an error
        return reply.text

    return StructuredTool.from_function(
        run,
        name=name,
        description=description,
        args_schema=schema.model_json_schema(),  # JSON, not the model class: TriageTools does the validating
        handle_tool_error=True,  # only ToolException: a ToolFailure goes on to the loop
    )
