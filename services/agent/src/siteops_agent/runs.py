"""Triage runs started over the API: queued, run one at a time by a worker thread, kept in memory (agent/8).

A run takes minutes (one LLM loop per window), longer than an HTTP request should wait: POST queues it and answers
at once, GET reads how it is going. The work itself is the job, the same `run_triage` the CLI calls.
One at a time protects the model server: parallel runs wouldn't finish sooner, and their calls would time out.
"""

import threading
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from siteops_agent.report import TriageReport

MAX_QUEUED = 5  # runs waiting behind the running one; one more is refused (QueueFull)
MAX_KEPT = 50  # runs remembered; past that, the oldest finished ones are forgotten (never a queued or running one)

RunState = Literal["queued", "running", "done", "failed"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TriageRequest(_Model):
    """Triage the logs of [since, until): a closed range, so the same request reads the same logs."""

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
    queued_at: AwareDatetime
    started_at: AwareDatetime | None = None  # when the worker took it
    finished_at: AwareDatetime | None = None
    report: TriageReport | None = None  # state "done"
    error: str | None = None  # state "failed": the run as a whole failed (a window's failure is in the report)


class QueueFull(Exception):
    """MAX_QUEUED runs are already waiting: try again once one has started."""

    def __init__(self) -> None:
        super().__init__(f"the triage queue is full ({MAX_QUEUED} runs waiting)")


Job = Callable[[str, TriageRequest], TriageReport]  # (run_id, request) → its report


class TriageRuns:
    """Queues runs, runs them one at a time in order, and remembers the last MAX_KEPT."""

    def __init__(self, job: Job, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._job = job
        self._now = now
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)  # the worker sleeps on it until a run is queued
        self._runs: OrderedDict[str, RunStatus] = OrderedDict()  # oldest first
        self._queue: deque[str] = deque()  # run ids waiting, first in first out
        self._finished: dict[str, threading.Event] = {}  # set when a run is done or failed
        threading.Thread(target=self._work, name="triage-worker", daemon=True).start()

    def start(self, request: TriageRequest) -> RunStatus:
        """Queue a run and return at once. Raises QueueFull when MAX_QUEUED runs are already waiting."""
        with self._lock:
            if len(self._queue) >= MAX_QUEUED:
                raise QueueFull()
            run = RunStatus(run_id=uuid.uuid4().hex[:12], state="queued", request=request, queued_at=self._now())
            self._runs[run.run_id] = run
            self._finished[run.run_id] = threading.Event()
            self._queue.append(run.run_id)
            self._forget_old()
            self._wake.notify()
        return run

    def get(self, run_id: str) -> RunStatus | None:
        with self._lock:
            return self._runs.get(run_id)

    def recent(self) -> list[RunStatus]:
        """Newest first."""
        with self._lock:
            return list(reversed(self._runs.values()))

    def wait(self, run_id: str, timeout: float | None = None) -> bool:
        """Block until the run is done or failed (for the tests and the CLI); False if the timeout passed first."""
        with self._lock:
            finished = self._finished.get(run_id)
        return finished.wait(timeout) if finished is not None else True

    def _work(self) -> None:
        """The worker: forever, take the oldest queued run and run it."""
        while True:
            with self._wake:
                while not self._queue:
                    self._wake.wait()
                run = self._runs[self._queue.popleft()].model_copy(update={"state": "running", "started_at": self._now()})
                self._runs[run.run_id] = run
            self._run(run)  # outside the lock: GET keeps answering while it runs

    def _run(self, run: RunStatus) -> None:
        try:
            report = self._job(run.run_id, run.request)
            finished = run.model_copy(update={"state": "done", "report": report, "finished_at": self._now()})
        except Exception as error:  # the run failed as a whole (e.g. no log file): recorded, the next one runs
            finished = run.model_copy(update={"state": "failed", "error": f"{type(error).__name__}: {error}", "finished_at": self._now()})
        with self._lock:
            self._runs[run.run_id] = finished
            self._finished[run.run_id].set()
            self._forget_old()

    def _forget_old(self) -> None:
        """Past MAX_KEPT, drop the oldest finished runs. Queued and running ones are always kept. Called under the lock."""
        excess = len(self._runs) - MAX_KEPT
        finished = [run_id for run_id, run in self._runs.items() if run.state in ("done", "failed")]  # oldest first
        for run_id in finished[: max(0, excess)]:
            del self._runs[run_id]
            del self._finished[run_id]
