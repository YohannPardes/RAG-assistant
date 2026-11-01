"""Triage runs started over the API: one at a time, in a worker thread, kept in memory (agent/8).

A run takes minutes (one LLM loop per window), longer than an HTTP request should wait: POST starts it and answers
at once, GET reads how it is going. The work itself is the job, the same `run_triage` the CLI calls.
"""

import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from siteops_agent.report import TriageReport

MAX_KEPT = 50  # finished runs remembered; older ones are forgotten (their reports are in the response that read them)

RunState = Literal["running", "done", "failed"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TriageRequest(_Model):
    """Triage the logs of [since, until): a closed range, so the same request reads the same logs (agent/13)."""

    since: AwareDatetime
    until: AwareDatetime
    dry_run: bool = False  # tickets go to the in-memory sink: nothing is filed

    @model_validator(mode="after")
    def _range_in_order(self) -> Self:
        if self.until <= self.since:
            raise ValueError("until must be after since")
        return self


class RunStatus(_Model):
    run_id: str
    state: RunState
    request: TriageRequest
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    report: TriageReport | None = None  # state "done"
    error: str | None = None  # state "failed": the run as a whole failed (a window's failure is in the report)


class RunInProgress(Exception):
    """A run is already going: one at a time (one model server, traces in order)."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id} is in progress")
        self.run_id = run_id


Job = Callable[[str, TriageRequest], TriageReport]  # (run_id, request) → its report


class TriageRuns:
    """Starts runs one at a time and remembers the last MAX_KEPT."""

    def __init__(self, job: Job, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._job = job
        self._now = now
        self._lock = threading.Lock()
        self._runs: OrderedDict[str, RunStatus] = OrderedDict()  # oldest first
        self._threads: dict[str, threading.Thread] = {}

    def start(self, request: TriageRequest) -> RunStatus:
        """Start a run in a worker thread and return at once. Raises RunInProgress while another runs."""
        with self._lock:
            if running := next((run for run in self._runs.values() if run.state == "running"), None):
                raise RunInProgress(running.run_id)
            run = RunStatus(run_id=uuid.uuid4().hex[:12], state="running", request=request, started_at=self._now())
            self._runs[run.run_id] = run
            while len(self._runs) > MAX_KEPT:
                self._runs.popitem(last=False)
            thread = threading.Thread(target=self._run, args=(run,), name=f"triage-{run.run_id}", daemon=True)
            self._threads[run.run_id] = thread
        thread.start()
        return run

    def get(self, run_id: str) -> RunStatus | None:
        with self._lock:
            return self._runs.get(run_id)

    def recent(self) -> list[RunStatus]:
        """Newest first."""
        with self._lock:
            return list(reversed(self._runs.values()))

    def wait(self, run_id: str, timeout: float | None = None) -> None:
        """Block until the run has finished (for the tests and the CLI)."""
        if thread := self._threads.get(run_id):
            thread.join(timeout)

    def _run(self, run: RunStatus) -> None:
        try:
            report = self._job(run.run_id, run.request)
            finished = run.model_copy(update={"state": "done", "report": report, "finished_at": self._now()})
        except Exception as error:  # the run failed as a whole (e.g. no log file): recorded, the service keeps serving
            finished = run.model_copy(update={"state": "failed", "error": f"{type(error).__name__}: {error}", "finished_at": self._now()})
        with self._lock:
            if run.run_id in self._runs:
                self._runs[run.run_id] = finished
            self._threads.pop(run.run_id, None)
