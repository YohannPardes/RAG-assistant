from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChunkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    source: str
    text: str
    heading_path: list[str]          # list, not tuple: it comes back from JSON as a list
    page: int | None
    embedding_model: str
    ingested_at: datetime