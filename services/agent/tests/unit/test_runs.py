import threading
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from siteops_agent.report import TriageReport
from siteops_agent.runs import MAX_KEPT, MAX_QUEUED, QueueFull, TriageRequest, TriageRuns

SINCE = datetime(2026, 11, 1, 6, tzinfo=UTC)
REQUEST = TriageRequest(since=SINCE, until=SINCE + timedelta(hours=4))


def report_for(run_id: str, request: TriageRequest) -> TriageReport:
    return TriageReport(run_id=run_id, since=request.since, until=request.until, model="fake", events_read=10, unparsable=0, outcomes=[])


class Gate:
    """A job that holds each run until the test opens the gate; records the order runs started in."""

    def __init__(self) -> None:
        self.started = threading.Semaphore(0)  # one release per run that started
        self.open = threading.Event()
        self.order: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, run_id: str, request: TriageRequest) -> TriageReport:
        self.order.append(run_id)
        self.started.release()
        self.open.wait(timeout=5)
        if self.fail is not None:
            raise self.fail
        return report_for(run_id, request)

    def wait_started(self) -> None:
        assert self.started.acquire(timeout=5), "no run started"


def test_a_run_is_queued_runs_then_holds_its_report() -> None:
    gate = Gate()
    runs = TriageRuns(gate)

    run = runs.start(REQUEST)
    assert run.state == "queued" and run.started_at is None
    gate.wait_started()
    assert runs.get(run.run_id).state == "running"  # type: ignore[union-attr]

    gate.open.set()
    assert runs.wait(run.run_id, timeout=5)
    done = runs.get(run.run_id)

    assert done is not None and done.state == "done" and done.started_at and done.finished_at
    assert done.report is not None and done.report.run_id == run.run_id  # the job got the run's id


def test_runs_wait_their_turn_and_run_one_at_a_time_in_order() -> None:
    gate = Gate()
    runs = TriageRuns(gate)
    first = runs.start(REQUEST)
    gate.wait_started()

    waiting = [runs.start(REQUEST) for _ in range(3)]

    assert [runs.get(run.run_id).state for run in waiting] == ["queued"] * 3  # type: ignore[union-attr]
    assert gate.order == [first.run_id]  # nothing else started while the first runs
    gate.open.set()
    for run in waiting:
        assert runs.wait(run.run_id, timeout=5)
    assert gate.order == [first.run_id, *(run.run_id for run in waiting)]  # first in, first out


def test_the_queue_holds_max_queued_runs_then_is_full() -> None:
    gate = Gate()
    runs = TriageRuns(gate)
    runs.start(REQUEST)
    gate.wait_started()  # one running, not counted in the queue

    queued = [runs.start(REQUEST) for _ in range(MAX_QUEUED)]
    with pytest.raises(QueueFull):
        runs.start(REQUEST)

    assert len(queued) == MAX_QUEUED
    gate.open.set()
    assert runs.wait(queued[-1].run_id, timeout=5)
    runs.start(REQUEST)  # room again once the queue has drained


def test_a_failing_run_is_recorded_and_the_next_one_still_runs() -> None:
    gate = Gate()
    gate.fail = FileNotFoundError("data/logs/site.log")
    runs = TriageRuns(gate)
    failing = runs.start(REQUEST)
    gate.wait_started()
    after = runs.start(REQUEST)

    gate.open.set()
    assert runs.wait(failing.run_id, timeout=5) and runs.wait(after.run_id, timeout=5)

    failed = runs.get(failing.run_id)
    assert failed is not None and failed.state == "failed" and failed.report is None
    assert failed.error == "FileNotFoundError: data/logs/site.log"
    assert runs.get(after.run_id).state == "failed"  # type: ignore[union-attr]  # it ran: the worker survived the first failure


def test_recent_runs_are_newest_first_and_bounded() -> None:
    runs = TriageRuns(report_for)
    ids = []
    for _ in range(MAX_KEPT + 2):
        ids.append(runs.start(REQUEST).run_id)
        assert runs.wait(ids[-1], timeout=5)

    recent = [run.run_id for run in runs.recent()]

    assert recent == list(reversed(ids))[:MAX_KEPT]
    assert runs.get(ids[0]) is None  # the oldest are forgotten


def test_queued_and_running_runs_are_never_forgotten() -> None:
    # MAX_KEPT counts finished runs out first: a full registry must not drop work that hasn't happened yet
    gate = Gate()
    gate.open.set()
    runs = TriageRuns(gate)
    for _ in range(MAX_KEPT):
        assert runs.wait(runs.start(REQUEST).run_id, timeout=5)
    gate.open.clear()
    running = runs.start(REQUEST)
    for _ in range(MAX_KEPT):
        gate.wait_started()  # drain the semaphore of the finished runs
    gate.wait_started()
    queued = [runs.start(REQUEST) for _ in range(MAX_QUEUED)]

    assert runs.get(running.run_id).state == "running"  # type: ignore[union-attr]
    assert all(runs.get(run.run_id).state == "queued" for run in queued)  # type: ignore[union-attr]
    assert len(runs.recent()) == MAX_KEPT
    gate.open.set()


@pytest.mark.parametrize(
    "fields",
    [
        {"since": SINCE, "until": SINCE},
        {"since": SINCE, "until": SINCE - timedelta(1)},
        {"since": SINCE.replace(tzinfo=None), "until": SINCE + timedelta(1)},
    ],
    ids=["empty-range", "reversed", "naive-time"],
)
def test_a_request_must_be_a_closed_aware_range_in_order(fields) -> None:
    with pytest.raises(ValidationError):
        TriageRequest(**fields)
