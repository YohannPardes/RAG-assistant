from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Self

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict

from siteops_agent.api.errors import ApiError
from siteops_agent.models import Window

Preview = Callable[[datetime, datetime], list[Window]]  # the windows a run of [since, until) would triage


class WindowSummary(BaseModel):
    """A window without its events (no log text leaves through a preview): what /windows lists."""

    model_config = ConfigDict(frozen=True)

    window_id: str
    host: str
    rule: str
    start: datetime
    end: datetime
    events: int
    omitted: int

    @classmethod
    def of(cls, window: Window) -> Self:
        return cls(
            window_id=window.id,
            host=window.host,
            rule=window.rule,
            start=window.start,
            end=window.end,
            events=len(window.events),
            omitted=window.omitted,
        )


def router(preview: Preview) -> APIRouter:
    routes = APIRouter()

    @routes.get("/windows")
    def windows(since: Annotated[AwareDatetime, Query()], until: Annotated[AwareDatetime, Query()]) -> list[WindowSummary]:
        """The windows a run of [since, until) would triage: the prefilter only, no LLM, nothing filed."""
        if until <= since:
            raise ApiError(422, "invalid_request", "until must be after since")
        return [WindowSummary.of(window) for window in preview(since, until)]

    return routes
