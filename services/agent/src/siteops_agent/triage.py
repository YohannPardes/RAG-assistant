"""One triage run: the logs of [since, until) → windows → one investigation per window → the report.

Plain Python, no framework: how a window is investigated is passed in (TriageAgent.triage, wired by the container),
so this module doesn't import the agent and a test can pass a stub. The CLI calls it directly; the API through TriageRuns.
"""

from collections.abc import Callable, Set
from datetime import datetime

from siteops_agent.log_context import LogContext
from siteops_agent.log_source.base import LogSource
from siteops_agent.models import Window
from siteops_agent.prefilter import find_windows
from siteops_agent.report import TriageReport, WindowOutcome

TriageWindow = Callable[[Window, LogContext, Set[str]], WindowOutcome]  # TriageAgent.triage: never raises for a window


def run_triage(run_id: str, since: datetime, until: datetime, source: LogSource, run_triage_window: TriageWindow, model: str) -> TriageReport:
    """Triage the logs of [since, until): the same range reads the same events, so a run can be repeated."""
    # 1. the run's events, in time order
    batch = source.read(since, until)

    # 2. what every window of the run shares: the events to look around it, the servers that wrote them
    context = LogContext(batch.events)
    known_hosts = frozenset(event.host for event in batch.events)

    # 3. one investigation per window, in order; a failed window is an "error" outcome, the next one still runs
    # each triage window is a full agent run
    outcomes = [run_triage_window(window, context, known_hosts) for window in find_windows(batch.events)]

    return TriageReport(
        run_id=run_id,
        since=since,
        until=until,
        model=model,
        events_read=len(batch.events),
        unparsable=batch.unparsable,
        outcomes=outcomes,
    )
