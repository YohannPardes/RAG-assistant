import os
from pathlib import Path

import httpx
import pytest

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Folder holding the sample runbooks, one per supported format."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def qdrant_url() -> str:
    """URL of a running Qdrant server; the test is skipped without one (CI starts one as a service)."""
    try:
        httpx.get(f"{QDRANT_URL}/healthz", timeout=2).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"no Qdrant server at {QDRANT_URL}")
    return QDRANT_URL
