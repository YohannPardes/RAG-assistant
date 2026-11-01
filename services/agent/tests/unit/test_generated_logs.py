"""The generator and the parser agree on the line format: scripts/generate_logs.py is not a package, load it from its path."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from siteops_agent.log_parser.syslog import SyslogStyleParser

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "generate_logs.py"


@pytest.fixture(scope="module")
def generate_logs() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_logs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_generated_line_parses_and_keeps_its_fields(generate_logs) -> None:
    lines, _ = generate_logs.generate(seed=42, start=datetime(2026, 11, 1, 6, tzinfo=UTC), hours=2)
    text = [generate_logs.format_line(line) + "\n" for line in lines]

    events, unparsable = SyslogStyleParser().parse_lines(text)

    assert unparsable == 0
    # same time (to the millisecond the format keeps), level, host, component and message as generated
    assert [(e.time, e.level, e.host, e.component, e.message) for e in events] == [
        (t.replace(microsecond=t.microsecond // 1000 * 1000), level, host, service, message) for t, level, host, service, message in lines
    ]
