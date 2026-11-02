"""scripts/triage_eval.py's scoring on hand-made ground truths and reports, and run_eval.py's prefilter subcommand end to
end (it needs no LLM). The scripts are not a package: they are loaded from their path."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"

TRUTH = {
    "faults": [
        {
            "id": "F1",
            "fault_type": "pg_primary_down",
            "host": "db-primary-01",
            "affected_hosts": ["db-primary-01", "db-replica-02", "api-01"],
            "start": "2026-11-01T07:00:00Z",
            "end": "2026-11-01T07:20:00Z",
            "expected_runbook": "runbooks/runbook_db_failover.docx",
        },
        {
            "id": "F2",
            "fault_type": "disk_full",
            "host": "log-02",
            "affected_hosts": ["log-02"],
            "start": "2026-11-01T08:00:00Z",
            "end": "2026-11-01T08:10:00Z",
            "expected_runbook": "runbooks/runbook_disk_full.pdf",
        },
    ],
    "decoys": [{"id": "D1", "host": "api-02", "start": "2026-11-01T09:00:00Z", "end": "2026-11-01T09:00:00Z", "message": "504"}],
}


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def triage_eval() -> ModuleType:
    return load("triage_eval")


@pytest.fixture(scope="module")
def run_eval() -> ModuleType:
    return load("run_eval")


def window(host: str, start: str, end: str, rule: str = "rule") -> dict:
    return {"host": host, "rule": rule, "start": f"2026-11-01T{start}Z", "end": f"2026-11-01T{end}Z"}


def outcome(host: str, start: str, end: str, status: str, draft: tuple[str, str] | None = None, cites: str | None = None, offered=()) -> dict:
    """A triage outcome as report.json has it: the ticket's (type, host), the runbook it cites, what its searches offered."""
    searches = [{"query": "q", "chunk_ids": [f"c-{source}" for source in offered], "sources": list(offered)}]
    result = {**window(host, start, end), "status": status, "searches": searches, "llm_calls": 2, "duration_s": 10.0, "error": None}
    if draft:
        result["draft"] = {"fault_type": draft[0], "host": draft[1], "runbook_chunk_id": f"c-{cites}" if cites else None}
    if status == "dismissed":
        result["dismissal"] = {"reason": "transient"}
    return result


DB = "runbooks/runbook_db_failover.docx"
DISK = "runbooks/runbook_disk_full.pdf"


# --- what a window is on ---


@pytest.mark.parametrize(
    ("host", "start", "end", "expected"),
    [
        ("db-replica-02", "07:05:00", "07:10:00", ("fault", "F1")),  # an affected host, not the root one
        ("log-02", "08:10:30", "08:12:00", ("fault", "F2")),  # just after the fault: within the tolerance
        ("log-02", "08:30:00", "08:31:00", ("other", None)),  # same host, long after
        ("api-02", "08:59:30", "09:00:10", ("decoy", "D1")),
        ("api-01", "09:00:00", "09:00:10", ("other", None)),  # the decoy's time, another host
    ],
)
def test_a_window_is_on_a_fault_a_decoy_or_other(triage_eval, host, start, end, expected) -> None:
    assert triage_eval.classify(window(host, start, end), TRUTH) == expected


# --- prefilter ---


def test_prefilter_recall_and_the_prefilter_only_baseline(triage_eval) -> None:
    windows = [
        window("db-replica-02", "07:05:00", "07:10:00"),
        window("api-01", "07:06:00", "07:12:00"),  # the same fault, seen from another host
        window("api-02", "09:00:00", "09:00:00"),  # the decoy
        window("ingress-01", "06:30:00", "06:31:00"),  # noise
    ]

    seed = triage_eval.score_prefilter(TRUTH, windows)
    metrics = triage_eval.prefilter_metrics([{"seed": 1, **seed}])

    assert (metrics["prefilter_recall"], metrics["n_faults"]) == (0.5, 2)  # F2 got no window
    assert (metrics["windows_on_faults"], metrics["windows_on_decoys"], metrics["windows_on_other"]) == (2, 1, 1)
    assert metrics["baseline_precision"] == 0.5  # every window a ticket: 2 of 4 on a fault
    assert [f["windows"] for f in seed["per_fault"]] == [2, 0]
    assert seed["decoys_flagged"] == ["D1"]


# --- triage ---


def test_a_matching_ticket_detects_the_fault_and_more_are_duplicates(triage_eval) -> None:
    outcomes = [
        outcome("db-replica-02", "07:05:00", "07:10:00", "ticketed", ("pg_primary_down", "db-primary-01"), cites=DB, offered=[DB]),
        outcome("api-01", "07:06:00", "07:12:00", "ticketed", ("pg_primary_down", "api-01"), cites=DB, offered=[DB]),  # another host
        outcome("db-replica-02", "07:15:00", "07:18:00", "deduplicated", ("pg_primary_down", "db-primary-01"), offered=[DB]),
    ]

    f1 = triage_eval.score_triage(TRUTH, outcomes)["faults"][0]

    assert f1["detected"] and f1["duplicates"] == 1  # the deduplicated one is absorbed by the sink, not a duplicate
    assert f1["root_host"] and f1["runbook_cited"] and f1["offered"]


def test_a_missed_fault_is_blamed_on_the_first_stage_that_lost_it(triage_eval) -> None:
    outcomes = [
        outcome("db-replica-02", "07:05:00", "07:10:00", "dismissed", offered=["runbooks/runbook_mysql_failover.pdf"]),  # never offered
    ]
    faults = triage_eval.score_triage(TRUTH, outcomes)["faults"]
    assert [(f["fault"], f["blame"]) for f in faults] == [("F1", "retrieval"), ("F2", "prefilter")]

    offered_then_dismissed = [outcome("db-replica-02", "07:05:00", "07:10:00", "dismissed", offered=[DB])]
    assert triage_eval.score_triage(TRUTH, offered_then_dismissed)["faults"][0]["blame"] == "model"


def test_a_ticket_of_the_wrong_type_is_a_false_positive_and_the_fault_stays_missed(triage_eval) -> None:
    outcomes = [outcome("db-replica-02", "07:05:00", "07:10:00", "ticketed", ("pg_replica_lag", "db-replica-02"), offered=[DB])]

    seed = triage_eval.score_triage(TRUTH, outcomes)

    assert not seed["faults"][0]["detected"] and seed["faults"][0]["blame"] == "model"
    assert [fp["on"] for fp in seed["false_positives"]] == ["fault"]
    assert (seed["tickets"], seed["true_tickets"]) == (1, 0)


def test_a_ticket_on_an_affected_host_detects_the_fault_but_not_its_root(triage_eval) -> None:
    outcomes = [outcome("db-replica-02", "07:05:00", "07:10:00", "ticketed", ("pg_primary_down", "db-replica-02"), offered=[DB])]

    f1 = triage_eval.score_triage(TRUTH, outcomes)["faults"][0]

    assert f1["detected"] and not f1["root_host"] and not f1["runbook_cited"]  # no runbook cited


def test_the_windows_matrix_and_the_decoys(triage_eval) -> None:
    outcomes = [
        outcome("api-02", "09:00:00", "09:00:00", "dismissed"),
        outcome("ingress-01", "06:30:00", "06:31:00", "unresolved"),
        outcome("log-02", "08:01:00", "08:09:00", "error"),
    ]

    seed = triage_eval.score_triage(TRUTH, outcomes)

    assert seed["matrix"]["decoy"]["dismissed"] == 1 and seed["matrix"]["other"]["unresolved"] == 1 and seed["matrix"]["fault"]["error"] == 1
    assert (seed["decoy_windows"], seed["decoys_dismissed"]) == (1, 1)
    assert [u["status"] for u in seed["unfinished"]] == ["unresolved", "error"]


def test_metrics_pool_the_seeds_and_repeats_show_their_spread(triage_eval) -> None:
    good = [outcome("db-replica-02", "07:05:00", "07:10:00", "ticketed", ("pg_primary_down", "db-primary-01"), cites=DB, offered=[DB])]
    seed_a = {"seed": 1, **triage_eval.score_triage(TRUTH, good)}
    seed_b = {"seed": 2, **triage_eval.score_triage(TRUTH, [])}

    once = triage_eval.triage_metrics([seed_a, seed_b])
    spread = triage_eval.over_repeats([once, triage_eval.triage_metrics([seed_b, seed_b])])

    assert (once["recall"], once["n_faults"], once["precision"]) == (0.25, 4, 1.0)
    assert (once["lost_at_prefilter"], once["lost_at_retrieval"], once["lost_at_model"]) == (3, 0, 0)
    assert spread["recall"] == {"mean": 0.125, "min": 0.0, "max": 0.25}
    assert spread["precision"] == {"mean": 1.0, "min": 1.0, "max": 1.0}  # the second repeat had no ticket: left out, not 0


def test_failures_list_every_kind_with_its_blame(triage_eval) -> None:
    outcomes = [
        outcome("db-replica-02", "07:05:00", "07:10:00", "ticketed", ("pg_primary_down", "db-replica-02"), offered=[DB]),
        outcome("ingress-01", "06:30:00", "06:31:00", "ticketed", ("cert_expiry", "ingress-01")),
    ]
    run = {"repeats": [{"repeat": 1, "seeds": [{"seed": 7, **triage_eval.score_triage(TRUTH, outcomes)}]}]}

    failures = {(f["item"], f["failure"], f["blame"]) for f in triage_eval.triage_failures(run)}

    assert failures == {
        ("seed 7 F1 pg_primary_down", "wrong root host", "model"),
        ("seed 7 F1 pg_primary_down", "wrong runbook cited", "model"),
        ("seed 7 F2 disk_full", "missed", "prefilter"),
        ("seed 7 ingress-01 rule 06:30:00–06:31:00", "false positive on other", "model"),
    }


def test_the_report_says_where_faults_were_lost_and_warns_when_retrieval_cant_be_scored(triage_eval) -> None:
    outcomes = [outcome("db-replica-02", "07:05:00", "07:10:00", "dismissed", offered=["runbook_db_failover.docx"])]  # old source names
    repeats = [{"repeat": 1, "seeds": [{"seed": 1, **triage_eval.score_triage(TRUTH, outcomes)}]}]
    config = {"model": "m", "seeds": [1], "repeats": 1, "hours": 2, "max_llm_calls": 8, "max_searches": 3, "max_log_queries": 2, "hits_per_search": 3}
    run = {
        "run_id": "triage-x",
        "git_commit": "abc",
        "config": config,
        "repeats": repeats,
        "metrics": triage_eval.over_repeats([triage_eval.triage_metrics(repeats[0]["seeds"])]),
        "warnings": triage_eval.warnings(repeats),
    }

    report = triage_eval.render_triage(run)

    assert "re-ingest data/" in report
    assert "| retrieval | 1 |" in report and "| prefilter | 1 |" in report
    assert "## Windows: what the prefilter flagged" in report and "| on fault | 0 | 0 | 1 | 0 | 0 |" in report


# --- run_eval.py ---


def test_held_out_questions_are_marked_and_a_config_flattens_into_columns(run_eval) -> None:
    assert run_eval.held_out({"held_out": True}) and not run_eval.held_out({})
    assert run_eval.flat_config({"seeds": [1, 2], "model": "m"}) == {"config.seeds": "1 2", "config.model": "m"}


def test_the_prefilter_eval_runs_end_to_end_and_records_itself(run_eval, tmp_path, monkeypatch, capsys) -> None:
    history = tmp_path / "history.jsonl"
    argv = ["run_eval.py", "prefilter", "--seeds", "1", "2", "--hours", "2", "--runs-dir", str(tmp_path / "runs"), "--history", str(history)]
    monkeypatch.setattr(sys, "argv", argv)

    run_eval.main()

    [folder] = (tmp_path / "runs").iterdir()
    assert {p.name for p in folder.iterdir()} == {"report.md", "failures.md", "report.json", "items.jsonl", "seed-1", "seed-2"}
    run = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    assert run["kind"] == "prefilter" and run["metrics"]["n_faults"] == 14 and run["metrics"]["prefilter_recall"] is not None
    items = [json.loads(line) for line in (folder / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(items) == 14 and items[0]["config.seeds"] == "1 2" and items[0]["run_id"] == run["run_id"]
    [line] = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    assert (line["run_id"], line["kind"], line["metrics"]) == (run["run_id"], "prefilter", run["metrics"])
    printed = capsys.readouterr().out
    assert "# Prefilter eval" in printed and "prefilter recall" in printed and str(folder) in printed


def test_a_window_on_one_fault_ticketed_as_another_is_a_misdiagnosis_even_when_it_counts_for_the_other(triage_eval) -> None:
    # api-01 is affected by F1 (pg_primary_down) too: a ticket there still "matches" F1, though the window was on F3
    truth = {
        **TRUTH,
        "faults": [
            *TRUTH["faults"],
            {
                **TRUTH["faults"][1],
                "id": "F3",
                "fault_type": "high_latency",
                "host": "api-01",
                "affected_hosts": ["api-01"],
                "start": "2026-11-01T10:00:00Z",
                "end": "2026-11-01T10:10:00Z",
            },
        ],
    }
    outcomes = [
        outcome("db-replica-02", "07:05:00", "07:10:00", "ticketed", ("pg_primary_down", "db-primary-01"), cites=DB, offered=[DB]),
        outcome("api-01", "10:02:00", "10:05:00", "ticketed", ("pg_primary_down", "api-01"), cites=DB, offered=[DB]),
    ]

    seed = triage_eval.score_triage(truth, outcomes)

    assert [(m["fault"], m["fault_type"]) for m in seed["misdiagnosed"]] == [("F3", "high_latency")]
    assert seed["faults"][0]["duplicates"] == 1 and not seed["faults"][2]["detected"]  # counted for F1, F3 missed
    assert triage_eval.triage_metrics([{"seed": 1, **seed}])["misdiagnosed_windows"] == 1
