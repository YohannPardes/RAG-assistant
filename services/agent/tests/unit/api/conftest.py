"""The API on fakes: a triage job the tests release, and a preview that records what it was asked."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from siteops_agent.api.app import ApiParts, build_app
from siteops_agent.models import Window
from siteops_agent.report import TriageReport
from siteops_agent.runs import TriageRequest, TriageRuns

API_KEY = "agent-key-0123456789"
AUTH = {"authorization": f"Bearer {API_KEY}"}
SINCE = datetime(2026, 11, 1, 6, tzinfo=UTC)
RANGE = {"since": SINCE.isoformat(), "until": (SINCE + timedelta(hours=4)).isoformat()}


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
def client(runs, previewed, window) -> TestClient:
    def preview(since: datetime, until: datetime) -> list[Window]:
        previewed.append((since, until))
        return [window]

    return TestClient(build_app(ApiParts(runs=runs, preview=preview, api_key=API_KEY)))
