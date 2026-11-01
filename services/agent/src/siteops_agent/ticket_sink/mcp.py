import anyio
import httpx2  # the MCP SDK's HTTP client
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from pydantic import ValidationError

from siteops_agent.ticket_sink.base import TicketSink, TicketSinkError
from siteops_common.contracts import CreateTicketResult, TicketCreate

TOOL = "create_ticket"


class _Unreachable(Exception):
    """The request never reached the tickets service: safe to retry."""


class McpTicketSink(TicketSink):
    """The tickets service's create_ticket tool over streamable HTTP (arch/6), with its API key (agent/5).

    A plain MCP client session: the model never sees this tool, only the agent's narrow one (agent/10).
    Connection errors are retried (arch/7); a repeated create is harmless, since the service deduplicates it (agent/3).
    Synchronous: call it from sync code, as it runs its own event loop per call.
    """

    def __init__(self, url: str, api_key: str, timeout: float, retries: int) -> None:
        self.url = url  # e.g. http://tickets:8000/mcp
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self.timeout = timeout
        self.retries = retries

    def create(self, ticket: TicketCreate) -> CreateTicketResult:
        for attempt in range(self.retries + 1):
            try:
                return anyio.run(self._create, ticket)
            except _Unreachable as error:
                if attempt == self.retries:
                    raise TicketSinkError(f"tickets service unreachable after {attempt + 1} attempts: {error}") from error
        raise AssertionError("unreachable")  # the loop returns or raises

    async def _create(self, ticket: TicketCreate) -> CreateTicketResult:
        try:
            async with (
                httpx2.AsyncClient(headers=self._headers, timeout=self.timeout) as http,
                Client(streamable_http_client(self.url, http_client=http)) as client,
            ):
                result = await client.call_tool(TOOL, {"ticket": ticket.model_dump(mode="json")})
        except Exception as error:  # the SDK raises from task groups: the cause is a leaf of nested exception groups
            causes = _leaves(error)
            if any(isinstance(cause, (httpx2.ConnectError, httpx2.ConnectTimeout)) for cause in causes):
                raise _Unreachable(repr(causes[0])) from error
            raise TicketSinkError(f"{TOOL} failed (a wrong API key is one cause): {causes!r}") from error
        if result.is_error:
            text = " ".join(block.text for block in result.content if isinstance(block, TextContent))
            raise TicketSinkError(f"the tickets service refused the ticket: {text[:500]}")
        try:
            return CreateTicketResult.model_validate(result.structured_content)
        except ValidationError as error:
            raise TicketSinkError(f"{TOOL} sent an invalid reply: {error}") from error


def _leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaves(inner)]
    return [error]
