"""Every tool the tickets MCP server offers. Adding a tool = one method + its name in `register`."""

import inspect

from mcp.server.mcpserver import MCPServer

from siteops_common.contracts import CreateTicketResult, TicketCreate
from siteops_tickets.store import TicketStore


class TicketTools:
    """The server's tools"""

    def __init__(self, store: TicketStore) -> None:
        self._store = store

    def create_ticket(self, ticket: TicketCreate) -> CreateTicketResult:
        """Open an incident ticket for a confirmed fault that needs human action.

        A report with the same fault type and host as a ticket opened within the dedup window
        does not create a new one: that ticket is returned, with created=false.
        """
        # sync: the SDK runs it in a worker thread, so the SQLite write never blocks the event loop
        return self._store.create(ticket)

    def register(self, server: MCPServer) -> None:
        for tool in (self.create_ticket,):
            # the SDK would send the raw docstring, indentation included: the LLM reads the cleaned text
            server.add_tool(tool, description=inspect.cleandoc(tool.__doc__ or ""))