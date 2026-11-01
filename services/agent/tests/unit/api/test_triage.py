from datetime import timedelta

import pytest

from siteops_agent.runs import MAX_QUEUED, TriageRequest

from .conftest import AUTH, RANGE, SINCE


def test_a_run_is_started_then_followed_to_its_report(client, runs, job) -> None:
    started = client.post("/triage", json=RANGE | {"dry_run": True}, headers=AUTH)

    assert started.status_code == 202
    run_id = started.json()["run_id"]
    assert started.json()["state"] == "queued"  # the worker takes it next
    assert job.running.wait(timeout=5)
    assert client.get(f"/triage/{run_id}", headers=AUTH).json()["state"] == "running"

    job.release.set()
    runs.wait(run_id, timeout=5)
    done = client.get(f"/triage/{run_id}", headers=AUTH).json()

    assert done["state"] == "done" and done["report"]["unparsable"] == 1
    assert done["report"]["totals"]["ticketed"] == 0  # the report as the CLI writes it
    assert job.requests == [TriageRequest(since=SINCE, until=SINCE + timedelta(hours=4), dry_run=True)]


def test_runs_wait_in_the_queue_until_it_is_full(client, runs, job) -> None:
    first = client.post("/triage", json=RANGE, headers=AUTH).json()["run_id"]
    assert job.running.wait(timeout=5)  # the first one runs, so it is not in the queue

    waiting = [client.post("/triage", json=RANGE, headers=AUTH) for _ in range(MAX_QUEUED)]
    full = client.post("/triage", json=RANGE, headers=AUTH)

    assert [(r.status_code, r.json()["state"]) for r in waiting] == [(202, "queued")] * MAX_QUEUED
    assert full.status_code == 503 and full.json()["code"] == "queue_full"
    assert full.headers["retry-after"] == "60"
    job.release.set()
    for run_id in [first, *(r.json()["run_id"] for r in waiting)]:
        assert runs.wait(run_id, timeout=5)
    assert len(job.requests) == 1 + MAX_QUEUED  # every accepted run ran; the refused one never did


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
    response = client.get("/triage/nope", headers=AUTH)

    assert response.status_code == 404 and response.json()["code"] == "run_not_found"


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
    response = client.post("/triage", json=body, headers=AUTH)

    assert response.status_code == 422 and response.json()["code"] == "invalid_request"
    assert job.requests == []
