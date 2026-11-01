import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from siteops_agent.api import build_app
from siteops_agent.models import Window
from siteops_agent.report import TriageReport
from siteops_agent.runs import TriageRequest, TriageRuns

API_KEY = "agent-key-0123456789"
AUTH = {"authorization": f"Bearer {API_KEY}"}
SINCE = datetime(2026, 11, 1, 6, tzinfo=UTC)
RANGE = {"since": SINCE.isoformat(), "until": (SINCE + timedelta(hours=4)).isoformat()}


class Job:
    """Finishes when the test says so; records what it was asked."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.requests: list[TriageRequest] = []

    def __call__(self, run_id: str, request: TriageRequest) -> TriageReport:
        self.requests.append(request)
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

    return TestClient(build_app(runs, preview, API_KEY))


def test_health_needs_no_key(client) -> None:
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers", [{}, {"authorization": "Bearer wrong-key-0123456789"}, {"authorization": API_KEY}], ids=["none", "wrong", "no-bearer"]
)
@pytest.mark.parametrize(("method", "path"), [("post", "/triage"), ("get", "/triage"), ("get", "/triage/x"), ("get", "/windows")])
def test_every_other_route_needs_the_key(client, job, headers, method, path) -> None:
    response = client.request(method, path, json=RANGE, headers=headers)

    assert response.status_code == 401
    assert job.requests == []  # refused before any work


def test_a_run_is_started_then_followed_to_its_report(client, runs, job) -> None:
    started = client.post("/triage", json=RANGE | {"dry_run": True}, headers=AUTH)

    assert started.status_code == 202
    run_id = started.json()["run_id"]
    assert started.json()["state"] == "running"
    assert client.get(f"/triage/{run_id}", headers=AUTH).json()["state"] == "running"

    job.release.set()
    runs.wait(run_id, timeout=5)
    done = client.get(f"/triage/{run_id}", headers=AUTH).json()

    assert done["state"] == "done" and done["report"]["unparsable"] == 1
    assert done["report"]["totals"]["ticketed"] == 0  # the report as the CLI writes it
    assert job.requests == [TriageRequest(since=SINCE, until=SINCE + timedelta(hours=4), dry_run=True)]


def test_a_second_run_while_one_is_going_is_a_conflict(client, runs, job) -> None:
    first = client.post("/triage", json=RANGE, headers=AUTH).json()["run_id"]

    second = client.post("/triage", json=RANGE, headers=AUTH)

    assert second.status_code == 409 and first in second.json()["detail"]
    job.release.set()
    runs.wait(first, timeout=5)


def test_the_run_list_is_newest_first_without_reports(client, runs, job) -> None:
    job.release.set()
    ids = []
    for _ in range(2):
        ids.append(client.post("/triage", json=RANGE, headers=AUTH).json()["run_id"])
        runs.wait(ids[-1], timeout=5)

    listed = client.get("/triage", headers=AUTH).json()

    assert [run["run_id"] for run in listed] == list(reversed(ids))
    assert all(run["report"] is None for run in listed)  # GET /triage/{id} has the report


def test_an_unknown_run_is_not_found(client) -> None:
    assert client.get("/triage/nope", headers=AUTH).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"since": SINCE.isoformat(), "until": SINCE.isoformat()},
        {"since": "2026-11-01T06:00:00", "until": "2026-11-01T10:00:00"},
        {"since": SINCE.isoformat()},
    ],
    ids=["empty-range", "naive-times", "no-until"],
)
def test_a_bad_range_is_refused_and_starts_nothing(client, job, body) -> None:
    assert client.post("/triage", json=body, headers=AUTH).status_code == 422
    assert job.requests == []


def test_windows_previews_the_prefilter_without_a_run(client, job, previewed, window) -> None:
    response = client.get("/windows", params=RANGE, headers=AUTH)

    [summary] = response.json()
    assert summary["window_id"] == window.id and summary["rule"] == "db_leader_unreachable"
    assert summary["events"] == len(window.events) and "raw" not in str(summary)  # counts, not log text
    assert previewed == [(SINCE, SINCE + timedelta(hours=4))]
    assert job.requests == []  # no run, no model


def test_windows_refuses_a_bad_range(client, previewed) -> None:
    assert client.get("/windows", params={"since": RANGE["until"], "until": RANGE["since"]}, headers=AUTH).status_code == 422
    assert client.get("/windows", params={"since": "2026-11-01T06:00:00", "until": "2026-11-01T10:00:00"}, headers=AUTH).status_code == 422
    assert previewed == []
