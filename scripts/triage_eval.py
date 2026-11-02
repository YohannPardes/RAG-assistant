"""Scoring for the prefilter and triage evals: pure functions over the ground truth and what a run produced (windows,
or a triage report as JSON). No LLM, no I/O, no import of the agent: run_eval.py runs things, this file judges them.

Every failure is blamed on one stage, in pipeline order:
  prefilter  no window covered the fault
  retrieval  a window did, but its searches never offered the expected runbook
  model      the runbook was offered, and the window was still dismissed, misdiagnosed or left unresolved

Matching (scope/4): a ticket matches a fault when it has the fault's type and its host is one of the fault's
affected hosts; time is not part of it (each fault type is seeded once per log). The first matching ticket detects
the fault; further ones are duplicates. A ticket that matches no fault is a false positive.
A window is "on" a fault when it is on one of the fault's affected hosts and overlaps its time span, "on" a decoy when
it is on the decoy's host and overlaps it; otherwise "other" (noise the prefilter flagged).
"""

from collections import Counter
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

TOLERANCE = timedelta(minutes=1)  # a window's span starts at its first matching event: allow a little on each side
STATUSES = ("ticketed", "deduplicated", "dismissed", "unresolved", "error")
KINDS = ("fault", "decoy", "other")

Truth = dict[str, Any]  # ground_truth.json: faults, decoys
Window = dict[str, Any]  # at least host, start, end (ISO strings); a triage outcome also has status, draft, searches...


def _t(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _overlaps(window: Window, start: str, end: str) -> bool:
    return _t(window["start"]) <= _t(end) + TOLERANCE and _t(window["end"]) >= _t(start) - TOLERANCE


def classify(window: Window, truth: Truth) -> tuple[str, str | None]:
    """What the window is on: ("fault", "F3"), ("decoy", "D2") or ("other", None)."""
    for fault in truth["faults"]:
        if window["host"] in fault["affected_hosts"] and _overlaps(window, fault["start"], fault["end"]):
            return "fault", fault["id"]
    for decoy in truth.get("decoys", []):
        if window["host"] == decoy["host"] and _overlaps(window, decoy["start"], decoy["end"]):
            return "decoy", decoy["id"]
    return "other", None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


# --- prefilter: which windows a log gets, before any LLM ---


def score_prefilter(truth: Truth, windows: list[Window]) -> dict[str, Any]:
    """Recall of the prefilter (the ceiling of everything after it) and the prefilter-only baseline: every window a
    ticket, so its precision is the share of windows that are on a fault."""
    kinds = [classify(window, truth) for window in windows]
    covered = {fault_id for kind, fault_id in kinds if kind == "fault"}
    per_fault = [
        {
            "fault": fault["id"],
            "fault_type": fault["fault_type"],
            "host": fault["host"],
            "windows": sum(1 for kind, fault_id in kinds if fault_id == fault["id"]),
        }
        for fault in truth["faults"]
    ]
    counts = Counter(kind for kind, _ in kinds)
    return {
        "faults": len(truth["faults"]),
        "covered": len(covered),
        "windows": len(windows),
        "on": {kind: counts[kind] for kind in KINDS},
        "decoys_flagged": sorted({fault_id for kind, fault_id in kinds if kind == "decoy" and fault_id}),
        "per_fault": per_fault,
    }


def prefilter_metrics(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    """The seeds pooled: one number per metric, over every seeded fault."""
    faults = sum(seed["faults"] for seed in seeds)
    windows = sum(seed["windows"] for seed in seeds)
    on = {kind: sum(seed["on"][kind] for seed in seeds) for kind in KINDS}
    return {
        "prefilter_recall": _rate(sum(seed["covered"] for seed in seeds), faults),
        "n_faults": faults,
        "windows": windows,
        "windows_on_faults": on["fault"],
        "windows_on_decoys": on["decoy"],
        "windows_on_other": on["other"],
        "baseline_precision": _rate(on["fault"], windows),  # prefilter only: every window a ticket
        "windows_per_fault": round(on["fault"] / faults, 2) if faults else None,
    }


# --- triage: what the agent decided for each window ---


def _cited_source(outcome: Window) -> str | None:
    """The runbook the ticket cites, by source: its chunk id looked up in the searches the window made."""
    chunk = (outcome.get("draft") or {}).get("runbook_chunk_id")
    for search in outcome.get("searches", []):
        for chunk_id, source in zip(search["chunk_ids"], search["sources"], strict=False):
            if chunk_id == chunk:
                return str(source)
    return None


def _offered(outcome: Window) -> set[str]:
    return {source for search in outcome.get("searches", []) for source in search["sources"]}


def _matches(outcome: Window, fault: dict[str, Any]) -> bool:
    draft = outcome.get("draft") or {}
    host = str(draft.get("host", "")).strip().lower()
    return draft.get("fault_type") == fault["fault_type"] and host in {h.lower() for h in fault["affected_hosts"]}


def score_triage(truth: Truth, outcomes: list[Window]) -> dict[str, Any]:
    """One triage run over one log: per fault, per window, and the counts the metrics are made of."""
    kinds = [classify(outcome, truth) for outcome in outcomes]
    tickets = [outcome for outcome in outcomes if outcome["status"] in ("ticketed", "deduplicated")]
    faults = []
    for fault in truth["faults"]:
        on_it = [outcome for outcome, (_, fault_id) in zip(outcomes, kinds, strict=True) if fault_id == fault["id"]]
        matching = [ticket for ticket in tickets if _matches(ticket, fault)]
        new = [ticket for ticket in matching if ticket["status"] == "ticketed"]
        offered = any(fault["expected_runbook"] in _offered(outcome) for outcome in on_it)
        detected = bool(matching)
        if detected:
            blame = None
        elif not on_it:
            blame = "prefilter"
        elif not offered:
            blame = "retrieval"
        else:
            blame = "model"
        faults.append(
            {
                "fault": fault["id"],
                "fault_type": fault["fault_type"],
                "host": fault["host"],
                "expected_runbook": fault["expected_runbook"],
                "windows": len(on_it),
                "offered": offered,
                "detected": detected,
                "duplicates": max(0, len(new) - 1),
                "root_host": detected and any(str(t["draft"]["host"]).strip().lower() == fault["host"].lower() for t in matching),
                "runbook_cited": detected and any(_cited_source(t) == fault["expected_runbook"] for t in matching),
                "blame": blame,
                "what_happened": [_describe(outcome) for outcome in on_it],
            }
        )
    false_positives = [
        {"window": _where(outcome), "on": kind, "ticket": _describe(outcome)}
        for outcome, (kind, _) in zip(outcomes, kinds, strict=True)
        if outcome["status"] == "ticketed" and not any(_matches(outcome, fault) for fault in truth["faults"])
    ]
    by_id = {fault["id"]: fault for fault in truth["faults"]}
    misdiagnosed = [
        {"window": _where(outcome), "fault": fault_id, "fault_type": by_id[fault_id]["fault_type"], "ticket": _describe(outcome)}
        for outcome, (kind, fault_id) in zip(outcomes, kinds, strict=True)
        if kind == "fault" and fault_id and outcome["status"] in ("ticketed", "deduplicated") and not _matches(outcome, by_id[fault_id])
    ]
    matrix = {kind: {status: 0 for status in STATUSES} for kind in KINDS}
    for outcome, (kind, _) in zip(outcomes, kinds, strict=True):
        matrix[kind][outcome["status"]] += 1
    new_tickets = [outcome for outcome in outcomes if outcome["status"] == "ticketed"]
    return {
        "faults": faults,
        "false_positives": false_positives,
        "misdiagnosed": misdiagnosed,  # a window on one fault, ticketed as another: may still count as that other's duplicate
        "matrix": matrix,
        "tickets": len(new_tickets),
        "true_tickets": sum(1 for t in new_tickets if any(_matches(t, fault) for fault in truth["faults"])),
        "decoy_windows": sum(matrix["decoy"].values()),
        "decoys_dismissed": matrix["decoy"]["dismissed"],
        "unfinished": [
            {"window": _where(o), "status": o["status"], "error": o.get("error")} for o in outcomes if o["status"] in ("unresolved", "error")
        ],
        "llm_calls": sum(outcome.get("llm_calls", 0) for outcome in outcomes),
        "windows": len(outcomes),
        "duration_s": round(sum(outcome.get("duration_s", 0.0) for outcome in outcomes), 1),
    }


def _where(outcome: Window) -> str:
    return f"{outcome['host']} {outcome['rule']} {outcome['start'][11:19]}–{outcome['end'][11:19]}"


def _describe(outcome: Window) -> str:
    """What the agent did with a window, in one line."""
    status = outcome["status"]
    if status in ("ticketed", "deduplicated"):
        draft = outcome["draft"]
        return f"{status} {draft['fault_type']} on {draft['host']} (cites {_cited_source(outcome) or 'no runbook'})"
    if status == "dismissed":
        return f"dismissed: {(outcome.get('dismissal') or {}).get('reason', '')[:120]}"
    if status == "error":
        return f"error: {str(outcome.get('error'))[:120]}"
    return f"unresolved after {outcome.get('llm_calls', 0)} model calls"


def triage_metrics(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    """One repeat over every seed, pooled: one number per metric, over every seeded fault."""
    faults = [fault for seed in seeds for fault in seed["faults"]]
    detected = [fault for fault in faults if fault["detected"]]
    tickets = sum(seed["tickets"] for seed in seeds)
    blame = Counter(fault["blame"] for fault in faults if fault["blame"])
    windows = sum(seed["windows"] for seed in seeds)
    return {
        "recall": _rate(len(detected), len(faults)),
        "precision": _rate(sum(seed["true_tickets"] for seed in seeds), tickets),
        "n_faults": len(faults),
        "tickets": tickets,
        "false_positives": sum(len(seed["false_positives"]) for seed in seeds),
        "duplicates": sum(fault["duplicates"] for fault in faults),
        "misdiagnosed_windows": sum(len(seed["misdiagnosed"]) for seed in seeds),
        "decoys_dismissed": _rate(sum(s["decoys_dismissed"] for s in seeds), sum(s["decoy_windows"] for s in seeds)),
        "root_host_right": _rate(sum(fault["root_host"] for fault in detected), len(detected)),
        "runbook_cited_right": _rate(sum(fault["runbook_cited"] for fault in detected), len(detected)),
        "runbook_offered": _rate(sum(fault["offered"] for fault in faults if fault["windows"]), sum(1 for f in faults if f["windows"])),
        "lost_at_prefilter": blame["prefilter"],
        "lost_at_retrieval": blame["retrieval"],
        "lost_at_model": blame["model"],
        "unfinished_windows": sum(len(seed["unfinished"]) for seed in seeds),
        "windows": windows,
        "llm_calls_per_window": round(sum(seed["llm_calls"] for seed in seeds) / windows, 2) if windows else None,
        "seconds_per_window": round(sum(seed["duration_s"] for seed in seeds) / windows, 1) if windows else None,
    }


def over_repeats(repeats: list[dict[str, Any]]) -> dict[str, Any]:
    """Each metric's mean over the repeats, and its min and max when there are several: the LLM's run-to-run spread."""
    summary: dict[str, Any] = {}
    for key in repeats[0]:
        values = [run[key] for run in repeats if run[key] is not None]
        if not values:
            summary[key] = None
        elif len(repeats) == 1:
            summary[key] = values[0]
        else:
            summary[key] = {"mean": round(mean(values), 3), "min": min(values), "max": max(values)}
    return summary


# --- reports, for people ---


def table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join("" if cell is None else str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _value(value: Any) -> str:
    if isinstance(value, dict):
        return f"{value['mean']} (min {value['min']}, max {value['max']})"
    return "n/a" if value is None else str(value)


def render_prefilter(run: dict[str, Any]) -> str:
    m = run["metrics"]
    rows = [
        [seed["seed"], f["fault"], f["fault_type"], f["host"], f["windows"] or "**none: missed**"] for seed in run["seeds"] for f in seed["per_fault"]
    ]
    decoys = [[seed["seed"], ", ".join(seed["decoys_flagged"]) or "none"] for seed in run["seeds"]]
    return "\n\n".join(
        [
            f"# Prefilter eval {run['run_id']}",
            f"Seeds {run['config']['seeds']}, {run['config']['hours']} h of log each, commit {run['git_commit']}. No LLM: the windows only.",
            "## Headline",
            table(
                ["metric", "value", "what it means"],
                [
                    ["prefilter recall", m["prefilter_recall"], f"faults with at least one window, of {m['n_faults']}: the ceiling of triage recall"],
                    [
                        "windows",
                        m["windows"],
                        f"{m['windows_on_faults']} on faults, {m['windows_on_decoys']} on decoys, {m['windows_on_other']} on other noise",
                    ],
                    ["baseline precision", m["baseline_precision"], "prefilter only, every window a ticket: what the LLM has to beat"],
                    ["windows per fault", m["windows_per_fault"], "above 1: one fault is split over several windows (duplicate tickets risk)"],
                ],
            ),
            "## Per fault",
            table(["seed", "fault", "type", "root host", "windows on it"], rows),
            "## Decoys that got a window (the model has to dismiss them)",
            table(["seed", "decoys"], decoys),
        ]
    )


def render_triage(run: dict[str, Any]) -> str:
    m, config = run["metrics"], run["config"]
    repeats = run["repeats"]
    headline = [
        ["recall", _value(m["recall"]), f"seeded faults detected by a matching ticket, of {_value(m['n_faults'])}"],
        ["precision", _value(m["precision"]), f"tickets that match a fault, of {_value(m['tickets'])} tickets"],
        ["false positives", _value(m["false_positives"]), "tickets matching no fault"],
        ["duplicates", _value(m["duplicates"]), "extra tickets for a fault already detected"],
        ["misdiagnosed windows", _value(m["misdiagnosed_windows"]), "windows on one fault ticketed as another fault (or another host)"],
        ["decoys dismissed", _value(m["decoys_dismissed"]), "windows on a decoy that the model dismissed"],
        ["root host right", _value(m["root_host_right"]), "detected faults with a ticket on the root-cause host"],
        ["runbook cited right", _value(m["runbook_cited_right"]), "detected faults with a ticket citing the expected runbook"],
        ["runbook offered", _value(m["runbook_offered"]), "faults with a window whose searches returned the expected runbook"],
        ["unfinished windows", _value(m["unfinished_windows"]), "unresolved or error"],
        [
            "cost",
            f"{_value(m['llm_calls_per_window'])} model calls, {_value(m['seconds_per_window'])} s per window",
            f"{_value(m['windows'])} windows",
        ],
    ]
    blame = [
        ["prefilter", _value(m["lost_at_prefilter"]), "no window on the fault"],
        ["retrieval", _value(m["lost_at_retrieval"]), "windows, but the expected runbook was never in their searches"],
        ["model", _value(m["lost_at_model"]), "runbook offered, still dismissed, misdiagnosed or unresolved"],
    ]
    matrix: Counter[tuple[str, str]] = Counter()
    for repeat in repeats:
        for seed in repeat["seeds"]:
            for kind in KINDS:
                for status in STATUSES:
                    matrix[kind, status] += seed["matrix"][kind][status]
    matrix_rows = [[f"on {kind}", *(matrix[kind, status] for status in STATUSES)] for kind in KINDS]
    per_fault: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for repeat in repeats:
        for seed in repeat["seeds"]:
            for fault in seed["faults"]:
                per_fault.setdefault((seed["seed"], fault["fault"]), []).append(fault)
    fault_rows = []
    for (seed_number, fault_id), runs in sorted(per_fault.items()):
        first = runs[0]
        blames = Counter(r["blame"] for r in runs if r["blame"])
        fault_rows.append(
            [
                seed_number,
                fault_id,
                first["fault_type"],
                first["host"],
                f"{sum(r['detected'] for r in runs)}/{len(runs)}",
                first["windows"],
                "yes" if first["offered"] else "no",
                ", ".join(f"{stage} ×{n}" for stage, n in blames.items()) or "",
                "; ".join(first["what_happened"])[:160] or "no window",
            ]
        )
    false_positives = [
        [seed["seed"], fp["window"], fp["on"], fp["ticket"]] for repeat in repeats for seed in repeat["seeds"] for fp in seed["false_positives"]
    ]
    misdiagnosed = [
        [seed["seed"], m["window"], f"{m['fault']} {m['fault_type']}", m["ticket"]]
        for repeat in repeats
        for seed in repeat["seeds"]
        for m in seed["misdiagnosed"]
    ]
    sections = [
        f"# Triage eval {run['run_id']}",
        f"Model {config['model']}, seeds {config['seeds']} × {config['repeats']} repeat(s), {config['hours']} h of log each, "
        f"per window {config['max_llm_calls']} model calls, {config['max_searches']} searches, {config['max_log_queries']} log lookups, "
        f"{config['hits_per_search']} hits per search; commit {run['git_commit']}. Matching: same fault type, host among the fault's affected hosts.",
        "## Headline" + (" (mean over repeats, with the spread)" if config["repeats"] > 1 else ""),
        table(["metric", "value", "what it means"], headline),
        "## Where the missed faults were lost",
        table(["stage", "faults", "meaning"], blame),
        "## Windows: what the prefilter flagged × what the model decided (all repeats)",
        table(["window", *STATUSES], matrix_rows),
        "## Per fault",
        table(
            ["seed", "fault", "type", "root host", "detected", "windows", "runbook offered", "blamed on", "what happened (first repeat)"], fault_rows
        ),
        "## Misdiagnosed windows (on one fault, ticketed as something else)",
        table(["seed", "window", "on fault", "ticket"], misdiagnosed) if misdiagnosed else "None.",
        "## False positives",
        table(["seed", "window", "on", "ticket"], false_positives) if false_positives else "None.",
    ]
    if run.get("warnings"):
        sections.insert(1, "\n".join(f"> **Warning:** {warning}" for warning in run["warnings"]))
    return "\n\n".join(sections)


def triage_failures(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Every failure of the run, one per row, for error analysis and for comparing runs: missed faults, wrong root
    host or runbook on a detected one, false positives, unfinished windows."""
    rows = []
    for repeat_number, repeat in enumerate(run["repeats"], start=1):
        for seed in repeat["seeds"]:
            for fault in seed["faults"]:
                key = f"seed {seed['seed']} {fault['fault']} {fault['fault_type']}"
                if not fault["detected"]:
                    rows.append(
                        {
                            "item": key,
                            "repeat": repeat_number,
                            "failure": "missed",
                            "blame": fault["blame"],
                            "detail": "; ".join(fault["what_happened"]),
                        }
                    )
                else:
                    if not fault["root_host"]:
                        rows.append(
                            {
                                "item": key,
                                "repeat": repeat_number,
                                "failure": "wrong root host",
                                "blame": "model",
                                "detail": "; ".join(fault["what_happened"]),
                            }
                        )
                    if not fault["runbook_cited"]:
                        blame = "model" if fault["offered"] else "retrieval"
                        rows.append(
                            {
                                "item": key,
                                "repeat": repeat_number,
                                "failure": "wrong runbook cited",
                                "blame": blame,
                                "detail": "; ".join(fault["what_happened"]),
                            }
                        )
            for m in seed["misdiagnosed"]:
                item = f"seed {seed['seed']} {m['fault']} {m['fault_type']} ({m['window']})"
                rows.append({"item": item, "repeat": repeat_number, "failure": "misdiagnosed window", "blame": "model", "detail": m["ticket"]})
            for fp in seed["false_positives"]:
                rows.append(
                    {
                        "item": f"seed {seed['seed']} {fp['window']}",
                        "repeat": repeat_number,
                        "failure": f"false positive on {fp['on']}",
                        "blame": "model",
                        "detail": fp["ticket"],
                    }
                )
            for unfinished in seed["unfinished"]:
                rows.append(
                    {
                        "item": f"seed {seed['seed']} {unfinished['window']}",
                        "repeat": repeat_number,
                        "failure": unfinished["status"],
                        "blame": "model",
                        "detail": unfinished["error"] or "",
                    }
                )
    return rows


def render_failures(title: str, failures: list[dict[str, Any]], traces: str) -> str:
    """failures.md: the table to fill in during error analysis, one line per failure in the "why" column."""
    rows = [[f["item"], f.get("repeat", ""), f["failure"], f["blame"], str(f["detail"]).replace("|", "/")[:200], ""] for f in failures]
    return "\n\n".join(
        [
            f"# Failures of {title}",
            f"Traces: {traces}. Read each one, write in a few words why it failed, then group the reasons and fix the largest group.",
            table(["item", "repeat", "failure", "blamed on", "what the agent did", "why (to fill in)"], rows) if rows else "No failures.",
        ]
    )


def warnings(repeats: list[dict[str, Any]]) -> list[str]:
    """What would make part of a triage report meaningless, said at its top."""
    seeds = [seed for repeat in repeats for seed in repeat["seeds"]]
    windows = sum(seed["windows"] for seed in seeds)
    unfinished = sum(len(seed["unfinished"]) for seed in seeds)
    searched = sum(1 for seed in seeds for fault in seed["faults"] if fault["windows"])
    offered = sum(1 for seed in seeds for fault in seed["faults"] if fault["offered"])
    found = []
    if windows and unfinished == windows:
        found.append("every window ended unresolved or in error: is the model server or the rag service down? See the transcripts.")
    elif searched and not offered:
        found.append(
            "no window's searches ever returned an expected runbook. If the collection was ingested from data/runbooks, its sources lack "
            "the runbooks/ prefix the ground truth uses: re-ingest data/ (scripts/ingest.py) before trusting 'runbook offered' and the blame."
        )
    return found
