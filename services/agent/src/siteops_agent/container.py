"""Composition root: the only module that reads Settings and picks the adapters. No logic: what is connected to what.

build_job gives the triage job, the same for the CLI (called directly) and the API (TriageRuns runs it).
Adapters with optional dependencies (langchain-openai, mcp, httpx) are imported where they are built.
"""

from pydantic import SecretStr

from siteops_agent.agent import TriageAgent
from siteops_agent.config import Settings
from siteops_agent.log_source.file import FileLogSource
from siteops_agent.rag_client.base import RagClient
from siteops_agent.report import TriageReport
from siteops_agent.runs import Job, TriageRequest
from siteops_agent.ticket_sink.base import TicketSink
from siteops_agent.ticket_sink.memory import MemoryTicketSink
from siteops_agent.triage import run_triage
from siteops_agent.triage_tools import ToolLimits


def build_job(settings: Settings | None = None) -> Job:
    """The triage job: (run_id, request) → its report. A dry run files into a fresh in-memory sink, so nothing is filed
    and one dry run's tickets don't deduplicate the next one's."""
    settings = settings or Settings()
    rag = _rag(settings)
    sink = _sink(settings)
    source = FileLogSource(settings.logs.path)

    def job(run_id: str, request: TriageRequest) -> TriageReport:
        agent = _agent(settings, rag, MemoryTicketSink() if request.dry_run else sink)
        return run_triage(run_id, request.since, request.until, source, agent.triage, settings.llm.model)

    return job


def _agent(settings: Settings, rag: RagClient, sink: TicketSink) -> TriageAgent:
    from langchain_openai import ChatOpenAI

    llm, triage = settings.llm, settings.triage
    model = ChatOpenAI(  # field names, not their aliases (base_url, model, api_key, timeout): the ones mypy checks
        openai_api_base=llm.base_url,
        model_name=llm.model,
        openai_api_key=SecretStr("EMPTY"),  # local model servers need none, but the client requires one
        temperature=llm.temperature,
        seed=llm.seed,
        reasoning_effort=llm.reasoning_effort,
        request_timeout=llm.timeout,
        max_retries=llm.max_retries,
    )
    limits = ToolLimits(max_searches=triage.max_searches, max_log_queries=triage.max_log_queries, hits_per_search=triage.hits_per_search)
    return TriageAgent(model, rag, sink, limits, triage.max_llm_calls)


def _rag(settings: Settings) -> RagClient:
    from siteops_agent.rag_client.http import HttpRagClient

    return HttpRagClient(settings.rag.base_url, settings.rag.timeout, settings.rag.retries)


def _sink(settings: Settings) -> TicketSink:
    from siteops_agent.ticket_sink.mcp import McpTicketSink

    tickets = settings.tickets
    return McpTicketSink(tickets.url, tickets.api_key.get_secret_value(), tickets.timeout, tickets.retries)
