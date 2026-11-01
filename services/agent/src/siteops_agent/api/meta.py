from collections.abc import Callable

from fastapi import APIRouter
from pydantic import AwareDatetime, BaseModel, ConfigDict


class LogRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    earliest: AwareDatetime | None  # None: no log readable yet
    latest: AwareDatetime | None


class Meta(BaseModel):
    """What the frontend reads once at startup: what to offer, and what to hide."""

    model_config = ConfigDict(frozen=True)

    model: str
    log_range: LogRange  # bounds the time picker
    range_presets: list[str]  # e.g. ["1h", "6h", "24h"]
    tickets_enabled: bool  # False: no tickets service configured, so "Write a ticket" is hidden


def router(meta: Callable[[], Meta]) -> APIRouter:
    routes = APIRouter()

    @routes.get("/meta")
    def get_meta() -> Meta:
        return meta()  # computed per call: the log range grows while the service runs

    return routes
