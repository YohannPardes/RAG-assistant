from fastapi import APIRouter

from siteops_agent.api.errors import ApiError
from siteops_agent.runs import QueueFull, RunStatus, TriageRequest, TriageRuns


def router(runs: TriageRuns) -> APIRouter:
    routes = APIRouter()

    @routes.post("/triage", status_code=202)
    def start_triage(request: TriageRequest) -> RunStatus:
        """Queue a run of [since, until) and return at once; follow it with GET /triage/{run_id}.
        503 when the queue is full: the server is at capacity for now, not the request at fault."""
        try:
            return runs.start(request)
        except QueueFull as error:
            raise ApiError(503, "queue_full", str(error), headers={"Retry-After": "60"}) from error

    @routes.get("/triage")
    def list_runs() -> list[RunStatus]:
        """The recent runs, newest first, without their reports."""
        return [run.model_copy(update={"report": None}) for run in runs.recent()]

    @routes.get("/triage/{run_id}")
    def get_run(run_id: str) -> RunStatus:
        if (run := runs.get(run_id)) is None:
            raise ApiError(404, "run_not_found", f"no run {run_id}")
        return run

    return routes
