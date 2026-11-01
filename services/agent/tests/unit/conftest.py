import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from siteops_agent.log_parser.syslog import SyslogStyleParser
from siteops_agent.models import Window
from siteops_common.contracts import SearchHit

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "generate_logs.py"


@pytest.fixture(scope="session")
def generate_logs() -> ModuleType:
    """scripts/generate_logs.py, loaded from its path: the script is not a package."""
    spec = importlib.util.spec_from_file_location("generate_logs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WINDOW_LINES = [  # pg_primary_down seen from the replica (scope/2): the root-cause host is only named in messages
    "2026-11-01T09:31:00.000Z INFO db-replica-02 pg-monitor: replica lag 2 MB, replay_lag 0.4s",
    "2026-11-01T09:31:11.000Z WARN db-replica-02 patroni: Request failed to db-primary-01: GET http://10.0.4.21:8008/patroni (Connection refused)",
    "2026-11-01T09:31:31.000Z WARN db-replica-02 patroni: leader db-primary-01 unreachable; no automatic failover, cluster paused",
    "2026-11-01T09:31:52.000Z WARN db-replica-02 patroni: Request failed to db-primary-01: GET http://10.0.4.21:8008/patroni (Connection refused)",
]


@pytest.fixture
def window() -> Window:
    events, _ = SyslogStyleParser().parse_lines(WINDOW_LINES)
    return Window("db-replica-02", "db_leader_unreachable", events[1].time, events[-1].time, tuple(events))


@pytest.fixture
def hits() -> dict[str, SearchHit]:
    """What search_runbooks returned for the window, by chunk_id."""
    failover = SearchHit(
        chunk_id="c-failover", source="runbook_db_failover.docx", heading_path=["DB failover", "Primary down"], page=None, text="…", score=0.8
    )
    lag = SearchHit(chunk_id="c-lag", source="runbook_pg_replication_lag.html", heading_path=["Replication lag"], page=None, text="…", score=0.6)
    return {hit.chunk_id: hit for hit in (failover, lag)}
