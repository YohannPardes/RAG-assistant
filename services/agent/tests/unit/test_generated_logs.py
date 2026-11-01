"""scripts/generate_logs.py: the triage eval's input and ground truth (scope/2, scope/4), and its agreement with the parser.

The script is not a package: load it from its path.
"""

import importlib.util
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import get_args

import pytest

from siteops_agent.log_parser.syslog import SyslogStyleParser
from siteops_common.contracts import FaultType

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "generate_logs.py"
START = datetime(2026, 11, 1, 6, tzinfo=UTC)
HOURS = 4


@pytest.fixture(scope="module")
def generate_logs() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_logs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generated(generate_logs):
    return generate_logs.generate(seed=42, start=START, hours=HOURS)


def fault_windows(faults) -> list[tuple[str, datetime, datetime]]:
    return [(f["fault_type"], datetime.fromisoformat(f["start"]), datetime.fromisoformat(f["end"])) for f in faults]


def test_every_generated_line_parses_and_keeps_its_fields(generate_logs, generated) -> None:
    lines, _ = generated
    text = [generate_logs.format_line(line) + "\n" for line in lines]

    events, unparsable = SyslogStyleParser().parse_lines(text)

    assert unparsable == 0
    # same time (to the millisecond the format keeps), level, host, component and message as generated
    assert [(e.time, e.level, e.host, e.component, e.message) for e in events] == [
        (t.replace(microsecond=t.microsecond // 1000 * 1000), level, host, service, message) for t, level, host, service, message in lines
    ]


def test_the_same_seed_gives_the_same_log_and_faults(generate_logs, generated) -> None:
    # the eval compares runs: the same arguments must give the same files
    assert generate_logs.generate(seed=42, start=START, hours=HOURS) == generated
    assert generate_logs.generate(seed=43, start=START, hours=HOURS)[0] != generated[0]


def test_every_fault_is_seeded_once_inside_the_log_and_none_overlap(generate_logs, generated) -> None:
    _, faults = generated
    windows = fault_windows(faults)

    assert sorted(fault_type for fault_type, _, _ in windows) == sorted(f.fault_type for f in generate_logs.FAULTS)
    assert all(START <= start < end <= START + timedelta(hours=HOURS) for _, start, end in windows)
    # sorted by start, and each one ends before the next starts: a ticket can match only one fault
    assert all(end <= next_start for (_, _, end), (_, next_start, _) in zip(windows, windows[1:], strict=False))


def test_a_fault_writes_only_inside_its_window_and_on_its_affected_hosts(generate_logs) -> None:
    start, end = START, START + timedelta(minutes=15)

    for fault in generate_logs.FAULTS:
        lines = fault.emit(random.Random(0), start, end)

        assert lines, fault.fault_type
        assert all(start <= t < end for t, *_ in lines), fault.fault_type
        # an affected host the fault never writes to is allowed (a silent primary), an unlisted one is not
        assert {host for _, _, host, _, _ in lines} <= set(fault.affected_hosts), fault.fault_type
        assert fault.affected_hosts[0] == fault.host, fault.fault_type


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_outside_the_faults_only_decoys_are_above_info(generate_logs, seed) -> None:
    # normal noise is INFO only, so every WARN or ERROR outside a fault is a planned decoy: a ticket for it is a false positive
    lines, faults = generate_logs.generate(seed=seed, start=START, hours=HOURS)
    windows = fault_windows(faults)
    margin = timedelta(minutes=5)

    outside = [t for t, level, *_ in lines if level != "INFO" and not any(start <= t < end for _, start, end in windows)]

    assert len(outside) == int(HOURS * 2)  # each decoy has exactly one line above INFO
    # far enough from every fault that no window-matching rule can mix a decoy into a fault
    assert all(not (start - margin <= t < end + margin) for t in outside for _, start, end in windows)


@pytest.mark.parametrize(
    ("fault_type", "host", "forbidden"),
    [
        ("pg_primary_down", "db-primary-01", ""),  # down: no line at all
        ("pg_primary_down", "db-replica-02", "following a leader"),  # its usual "all fine" line
        ("cert_expiry", "probe-eu-1", "healthz ok"),  # the probe can't succeed on an expired certificate
    ],
    ids=["primary-down", "replica-no-leader", "probe-no-success"],
)
def test_a_silenced_host_logs_no_normal_noise_during_its_fault(generated, fault_type, host, forbidden) -> None:
    lines, faults = generated
    _, start, end = next(window for window in fault_windows(faults) if window[0] == fault_type)

    during = [message for t, _, line_host, _, message in lines if line_host == host and start <= t < end]

    assert not [message for message in during if forbidden in message]


def test_every_seeded_fault_type_is_one_a_ticket_can_carry(generate_logs) -> None:
    # the eval matches a ticket's fault_type to the ground truth's (scope/4): the two lists must agree
    assert {f.fault_type for f in generate_logs.FAULTS} == set(get_args(FaultType)) - {"other"}


def test_every_expected_runbook_is_a_runbook_of_the_corpus(generate_logs) -> None:
    missing = [f.expected_runbook for f in generate_logs.FAULTS if not (REPO_ROOT / "data" / "runbooks" / f.expected_runbook).is_file()]

    assert missing == []


def test_main_writes_the_log_and_its_ground_truth(generate_logs, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["generate_logs.py", "--hours", "2", "--out-dir", str(tmp_path)])

    generate_logs.main()

    assert b"\r\n" not in (tmp_path / "site.log").read_bytes()  # LF on every OS: the same bytes everywhere
    ground_truth = json.loads((tmp_path / "ground_truth.json").read_text(encoding="utf-8"))
    assert (ground_truth["seed"], ground_truth["start"], ground_truth["end"]) == (42, "2026-11-01T06:00:00Z", "2026-11-01T08:00:00Z")
    fault = next(f for f in ground_truth["faults"] if f["fault_type"] == "pg_primary_down")
    assert set(fault) == {"id", "fault_type", "host", "affected_hosts", "start", "end", "expected_runbook"}
    assert fault["host"] == "db-primary-01"
    assert {"db-replica-02", "api-01", "api-02", "api-03"} <= set(fault["affected_hosts"])  # its symptoms are elsewhere


def test_fewer_than_two_hours_is_refused(generate_logs, monkeypatch) -> None:
    # below 2 hours the fault slots get too short to tell faults apart
    monkeypatch.setattr(sys, "argv", ["generate_logs.py", "--hours", "1"])

    with pytest.raises(SystemExit):
        generate_logs.main()
