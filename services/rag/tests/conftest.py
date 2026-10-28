from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Folder holding the sample runbooks, one per supported format."""
    return Path(__file__).parent / "fixtures"
