import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "generate_logs.py"


@pytest.fixture(scope="session")
def generate_logs() -> ModuleType:
    """scripts/generate_logs.py, loaded from its path: the script is not a package."""
    spec = importlib.util.spec_from_file_location("generate_logs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
