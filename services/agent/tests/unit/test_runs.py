import threading
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from siteops_agent.report import TriageReport
from siteops_agent.runs import MAX_KEPT, RunInProgress, TriageRequest, TriageRuns

SINCE = datetime(2026, 11, 1, 6, tzinfo=UTC)
REQUEST = TriageRequest(since=SINCE, until=SINCE + timedelta(hours=4))


def report_for(run_id: str, request: TriageRequest) -> TriageReport:
    return TriageReport(run_id=run_id, since=request.since, until=request.until, model="fake", events_read=10, unparsable=0, outcomes=[])


class ControlledJob:
    """A job that runs until the test releases it."""

    def __init__(self) -> None:
        self.started, self.release = threading.Event(), threading.Event()
        self.fail: Exception | None = None

    def __call__(self, run_id: str, request: TriageRequest) -> TriageReport:
        self.started.set()
        self.release.wait(timeout=5)
        if self.fail is not None:
            raise self.fail
        return report_for(run_id, request)


def test_a_run_starts_running_then_holds_its_report() -> None:
    job = ControlledJob()
    runs = TriageRuns(job)

    run = runs.start(REQUEST)
    job.started.wait(timeout=5)
    assert runs.get(run.run_id).state == "running"  # type: ignore[union-attr]

    job.release.set()
    runs.wait(run.run_id, timeout=5)
    done = runs.get(run.run_id)

    assert done is not None and done.state == "done" and done.finished_at is not None
    assert done.report is not None and done.report.run_id == run.run_id  # the job got the run's id


def test_only_one_run_at_a_time() -> None:
    job = ControlledJob()
    runs = TriageRuns(job)
    first = runs.start(REQUEST)

    with pytest.raises(RunInProgress) as refused:
        runs.start(REQUEST)

    assert refused.value.run_id == first.run_id
    job.release.set()
    runs.wait(first.run_id, timeout=5)
    assert runs.start(REQUEST).run_id != first.run_id  # the next one may start once it's done


def test_a_failing_run_is_recorded_and_the_next_one_can_start() -> None:
    job = ControlledJob()
    job.fail = FileNotFoundError("data/logs/site.log")
    job.release.set()
    runs = TriageRuns(job)

    run = runs.start(REQUEST)
    runs.wait(run.run_id, timeout=5)
    failed = runs.get(run.run_id)

    assert failed is not None and failed.state == "failed" and failed.report is None
    assert failed.error == "FileNotFoundError: data/logs/site.log"
    job.fail = None
    runs.wait(runs.start(REQUEST).run_id, timeout=5)


def test_recent_runs_are_newest_first_and_bounded() -> None:
    runs = TriageRuns(report_for)
    ids = []
    for _ in range(MAX_KEPT + 2):
        ids.append(runs.start(REQUEST).run_id)
        runs.wait(ids[-1], timeout=5)

    recent = [run.run_id for run in runs.recent()]

    assert recent == list(reversed(ids))[:MAX_KEPT]
    assert runs.get(ids[0]) is None  # the oldest are forgotten


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
