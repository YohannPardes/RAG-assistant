"""The API on fakes: a triage job the tests release, a fixed chat turn, an in-memory ticket sink, a real draft signer."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from siteops_agent.api.app import ApiParts, build_app
from siteops_agent.api.meta import LogRange, Meta
from siteops_agent.chat.base import ChatTurn, LogCitation, RunbookCitation, Step, TicketProposal
from siteops_agent.chat.fake import FakeChatEngine
from siteops_agent.drafts import DraftSigner
from siteops_agent.models import Window
from siteops_agent.report import TriageReport
from siteops_agent.runs import TriageRequest, TriageRuns
from siteops_agent.ticket_sink.memory import MemoryTicketSink
from siteops_common.contracts import TicketCreate

API_KEY = "agent-key-0123456789"
AUTH = {"authorization": f"Bearer {API_KEY}"}
SINCE = datetime(2026, 11, 1, 6, tzinfo=UTC)
RANGE = {"since": SINCE.isoformat(), "until": (SINCE + timedelta(hours=4)).isoformat()}
SECRET = b"s" * 32

TICKET = TicketCreate(
    fault_type="pg_primary_down",
    host="db-primary-01",
    severity="SEV-1",
    title="PostgreSQL primary db-primary-01 unreachable",
    summary="The replica cannot reach its primary and is not promoted: writes fail.",
    suggested_fix="Confirm the primary is down, then promote the replica.",
    evidence=["2026-11-01T09:31:31.000Z WARN db-replica-02 patroni: leader db-primary-01 unreachable; no automatic failover, cluster paused"],
    window_start=datetime(2026, 11, 1, 9, 31, 11, tzinfo=UTC),
    window_end=datetime(2026, 11, 1, 9, 45, 43, tzinfo=UTC),
)


def chat_turn(**changes: object) -> ChatTurn:
    """A checked turn citing a runbook [1] and a log line [2], with a ticket proposal pointing at both."""
    runbook = RunbookCitation(
        n=1,
        chunk_id="c-failover",
        source="runbook_db_failover.docx",
        heading_path=["Database failover", "Before promoting a replica"],
        page=2,
        quote="confirm the primary is unreachable",
        chunk_text="First, confirm the primary is unreachable from two hosts.",
        quote_start=7,
        quote_end=41,
    )
    log = LogCitation(
        n=2,
        host="db-replica-02",
        time=datetime(2026, 11, 1, 9, 31, 31, tzinfo=UTC),
        level="WARN",
        component="patroni",
        raw=TICKET.evidence[0],
        context=[],
    )
    turn = {
        "answered": True,
        "answer": "The primary is down [2]; confirm it before promoting [1].",
        "citations": [runbook, log],
        "steps": [Step(tool="search_runbooks", args={"query": "primary down"}, ok=True, summary="1 hit")],
        "proposal": TicketProposal(ticket=TICKET, evidence=[2], runbook=1),
        "model": "fake",
    }
    return ChatTurn.model_validate(turn | changes)


class Job:
    """A triage job that finishes when the test says so; records what it was asked."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.running = threading.Event()  # set once a run has started
        self.requests: list[TriageRequest] = []

    def __call__(self, run_id: str, request: TriageRequest) -> TriageReport:
        self.requests.append(request)
        self.running.set()
        self.release.wait(timeout=5)
        return TriageReport(run_id=run_id, since=request.since, until=request.until, model="fake", events_read=3, unparsable=1, outcomes=[])


@pytest.fixture
def job() -> Job:
    return Job()


@pytest.fixture
def runs(job) -> TriageRuns:
    return TriageRuns(job)


@pytest.fixture
def previewed() -> list[tuple[datetime, datetime]]:
    return []


@pytest.fixture
def engine() -> FakeChatEngine:
    return FakeChatEngine(chat_turn())


@pytest.fixture
def sink() -> MemoryTicketSink:
    return MemoryTicketSink()


@pytest.fixture
def drafts() -> DraftSigner:
    return DraftSigner(SECRET, ttl=timedelta(minutes=30))


@pytest.fixture
def meta() -> Meta:
    return Meta(
        model="fake", log_range=LogRange(earliest=SINCE, latest=SINCE + timedelta(hours=4)), range_presets=["1h", "6h", "24h"], tickets_enabled=True
    )


@pytest.fixture
def make_client(runs, previewed, window, engine, drafts, meta):
    """A client over the API; `sink=None` builds it without a tickets service."""

    def make(sink: MemoryTicketSink | None) -> TestClient:
        def preview(since: datetime, until: datetime) -> list[Window]:
            previewed.append((since, until))
            return [window]

        parts = ApiParts(runs=runs, preview=preview, engine=engine, drafts=drafts, sink=sink, meta=lambda: meta, api_key=API_KEY)
        return TestClient(build_app(parts))

    return make


@pytest.fixture
def client(make_client, sink) -> TestClient:
    return make_client(sink)
