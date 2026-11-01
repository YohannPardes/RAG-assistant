"""What the tickets MCP server offers: every tool lives here. Adding a tool = one more function in `register_tools`.

Each description is what an LLM reads to decide *when* to call the tool, so it says that, not just what the tool does.
Input and output schemas come from the `common` contracts (arch/4).
"""

from mcp.server.mcpserver import MCPServer

from siteops_common.contracts import CreateTicketResult, TicketCreate
from siteops_tickets.store import TicketStore

CREATE_TICKET_DESCRIPTION = (
    "Open an incident ticket for a confirmed fault that needs human action. "
    "A ticket with the same fault type and host as one opened within the dedup window is not stored again: "
    "that ticket comes back, with created=false."
)


def register_tools(server: MCPServer, store: TicketStore) -> None:
    @server.tool(description=CREATE_TICKET_DESCRIPTION)
    def create_ticket(ticket: TicketCreate) -> CreateTicketResult:
        # sync: the SDK runs it in a worker thread, so the SQLite write never blocks the event loop
        return store.create(ticket)
