"""Synthetic site logs with seeded faults (scope/2): the triage agent's input and the eval's ground truth.

Writes data/logs/site.log (normal noise from fake hosts, harmless one-off warnings, seeded faults) and
data/logs/ground_truth.json (one entry per seeded fault: type, host, affected hosts, start, end, expected runbook).
The one-off warnings ("decoys") are not faults: a ticket for one counts as a false positive.
Same seed and arguments give the same files.
Usage: uv run python scripts/generate_logs.py [--seed 42] [--hours 4] [--start 2026-11-01T06:00:00Z] [--out-dir data/logs]

Line format: <timestamp, ISO 8601 UTC, ms> <INFO|WARN|ERROR> <host> <service>: <message>
"""

import argparse
import json
import math
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
POD = "shop/checkout-worker-7d9f8-x2k4q"
# method, path, p50 ms, p99 target ms, timeout ms (RB-415's latency targets)
ENDPOINTS = [("GET", "/v2/orders", 40, 300, 2000), ("POST", "/v2/orders", 90, 800, 5000), ("GET", "/v2/search", 120, 1200, 5000)]

Line = tuple[datetime, str, str, str, str]  # time, level, host, service, message
Noise = Callable[[random.Random], tuple[str, str, str]]  # rng → level, service, message


def ticks(rng: random.Random, start: datetime, end: datetime, mean_seconds: float) -> Iterator[datetime]:
    """`start`, then times up to `end` with exponential gaps of mean `mean_seconds` (a Poisson process)."""
    t = start
    while t < end:
        yield t
        t += timedelta(seconds=rng.expovariate(1 / mean_seconds))


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def at(t: datetime, host: str, entry: tuple[str, str, str]) -> Line:
    level, service, message = entry
    return t, level, host, service, message


def format_line(line: Line) -> str:
    t, level, host, service, message = line
    return f"{t.strftime('%Y-%m-%dT%H:%M:%S')}.{t.microsecond // 1000:03d}Z {level} {host} {service}: {message}"


# --- normal noise, one generator per kind of host ---


def api_request(rng: random.Random, slow: float = 1.0, sigma: float = 0.4) -> tuple[str, str, str]:
    """One public-api access line; WARN above the endpoint's p99 target, ERROR at its timeout."""
    method, path, p50, p99, timeout = rng.choice(ENDPOINTS)
    ms = int(rng.lognormvariate(math.log(p50 * slow), sigma))
    if ms >= timeout:
        return "ERROR", "public-api", f"{method} {path} 504 upstream timeout after {timeout}ms"
    status = (200 if method == "GET" else 201) if rng.random() < 0.97 else rng.choice([404, 499])
    return ("WARN" if ms > p99 else "INFO"), "public-api", f"{method} {path} {status} {ms}ms"


def ingress_noise(rng: random.Random) -> tuple[str, str, str]:
    method, path, *_ = rng.choice(ENDPOINTS)
    status = 200 if rng.random() < 0.98 else rng.choice([301, 404])
    return "INFO", "ingress-nginx", f'"{method} {path} HTTP/2.0" {status} upstream=api/public-api request_time={rng.uniform(0.03, 0.2):.3f}'


def probe_noise(rng: random.Random) -> tuple[str, str, str]:
    return "INFO", "synthetics", f"check https://api.example.com/healthz ok {rng.randint(90, 160)}ms"


def primary_noise(rng: random.Random) -> tuple[str, str, str]:
    if rng.random() < 0.5:
        return "INFO", "patroni", "no action. I am (db-primary-01), the leader with the lock"
    return "INFO", "postgres", f"LOG: checkpoint complete: wrote {rng.randint(200, 4000)} buffers"


def replica_noise(rng: random.Random) -> tuple[str, str, str]:
    if rng.random() < 0.5:
        return "INFO", "patroni", "no action. I am (db-replica-02), a secondary, and following a leader (db-primary-01)"
    return "INFO", "pg-monitor", f"replica lag {rng.randint(0, 6)} MB, replay_lag {rng.uniform(0, 1.5):.1f}s"


def log_server_noise(rng: random.Random) -> tuple[str, str, str]:
    if rng.random() < 0.7:
        return "INFO", "rsyslogd", f"imtcp: received {rng.randint(800, 2500)} messages from {rng.randint(30, 60)} clients"
    return "INFO", "disk-monitor", f"/var/log usage {rng.randint(55, 66)}%"


def kubelet_noise(rng: random.Random) -> tuple[str, str, str]:
    messages = [
        f"Probe succeeded probeType=Readiness pod={POD}",
        f"SyncLoop UPDATE source=api pods=[{POD}]",
        "Image garbage collection: no images to remove",
    ]
    return "INFO", "kubelet", rng.choice(messages)


HOSTS: dict[str, tuple[float, Noise]] = {  # host → mean seconds between lines, noise generator
    "api-01": (6, api_request),
    "api-02": (6, api_request),
    "api-03": (6, api_request),
    "ingress-01": (8, ingress_noise),
    "probe-eu-1": (30, probe_noise),
    "db-primary-01": (45, primary_noise),
    "db-replica-02": (45, replica_noise),
    "log-01": (30, log_server_noise),
    "log-02": (30, log_server_noise),
    "k8s-node-03": (40, kubelet_noise),
}


# --- seeded faults, each matching a runbook ---


@dataclass(frozen=True)
class Fault:
    fault_type: str
    host: str  # where the fault is (root cause); other hosts may log its effects
    affected_hosts: tuple[str, ...]  # every host whose lines show the fault, `host` first (scope/4's matching rule)
    expected_runbook: str  # `source` of the runbook, as rag /search returns it
    emit: Callable[[random.Random, datetime, datetime], list[Line]]
    silences: tuple[str, ...]  # hosts whose normal noise stops during the fault (host down, or its lines replaced)


def disk_full(rng: random.Random, start: datetime, end: datetime) -> list[Line]:
    """/var/log fills up on log-02, then rsyslogd cannot write."""
    span = end - start
    lines: list[Line] = [
        (t, "WARN", "log-02", "disk-monitor", f"/var/log usage {88 + int(12 * ((t - start) / span))}%") for t in ticks(rng, start, end, 60)
    ]
    message = "action 'omfile' suspended: write error on /var/log/remote/app.log: No space left on device (errno 28)"
    lines += [(t, "ERROR", "log-02", "rsyslogd", message) for t in ticks(rng, start + span * 0.6, end, 15)]
    return lines


def pg_primary_down(rng: random.Random, start: datetime, end: datetime) -> list[Line]:
    """db-primary-01 goes silent; the replica cannot reach it and is not promoted; writes fail on the API."""
    lines: list[Line] = [
        (t, "WARN", "db-replica-02", "patroni", "Request failed to db-primary-01: GET http://10.0.4.21:8008/patroni (Connection refused)")
        for t in ticks(rng, start, end, 20)
    ]
    paused = "leader db-primary-01 unreachable; no automatic failover, cluster paused"
    lines += [(t, "WARN", "db-replica-02", "patroni", paused) for t in ticks(rng, start, end, 120)]
    message ="POST /v2/orders 500 orders-db write failed: FATAL: the database system is in recovery mode"
    lines += [(t, "ERROR", rng.choice(["api-01", "api-02", "api-03"]), "public-api", message) for t in ticks(rng, start, end, 10)]
    return lines


def pg_replica_lag(rng: random.Random, start: datetime, end: datetime) -> list[Line]:
    """Replay lag on db-replica-02 grows past RB-206's warning (64 MB) and critical (256 MB, 120 s) levels."""
    lines: list[Line] = []
    for t in ticks(rng, start, end, 30):
        progress = (t - start) / (end - start)
        mb, seconds = int(70 + 330 * progress), 10 + 170 * progress
        level = "ERROR" if mb > 256 or seconds > 120 else "WARN"
        lines.append((t, level, "db-replica-02", "pg-monitor", f"replica lag {mb} MB, replay_lag {seconds:.1f}s"))
    return lines


def cert_expiry(rng: random.Random, start: datetime, end: datetime) -> list[Line]:
    """The public certificate on ingress-01 expired after its renewal kept failing; the probe sees it."""
    lines: list[Line] = [
        (t, "WARN", "ingress-01", "cert-manager", "certificate edge/api-example-com renewal failed: 403 Forbidden from DNS provider")
        for t in ticks(rng, start, end, 300)
    ]
    handshake = "SSL_do_handshake() failed (SSL: error:0A000412:SSL routines::sslv3 alert bad certificate) while SSL handshaking"
    lines += [(t, "WARN", "ingress-01", "ingress-nginx", handshake) for t in ticks(rng, start, end, 20)]
    probe = "check https://api.example.com/healthz failed: x509: certificate has expired or is not yet valid"
    lines += [(t, "ERROR", "probe-eu-1", "synthetics", probe) for t in ticks(rng, start, end, 30)]
    return lines


def high_latency(rng: random.Random, start: datetime, end: datetime) -> list[Line]:
    """api-02 answers about 12x slower than usual: most requests above their p99 target, a few time out."""
    return [at(t, "api-02", api_request(rng, slow=12, sigma=0.6)) for t in ticks(rng, start, end, 6)]


def api_error_rate(rng: random.Random, start: datetime, end: datetime) -> list[Line]:
    """A bad release on api-03 fails about 20% of requests with a 500 (near miss of high_latency: same service)."""
    lines: list[Line] = [(start, "INFO", "api-03", "public-api", "starting public-api release v2.14.0")]
    for t in ticks(rng, start + timedelta(seconds=5), end, 6):
        if rng.random() < 0.2:
            lines.append((t, "ERROR", "api-03", "public-api", "POST /v2/orders 500 unhandled exception: KeyError: 'currency'"))
        else:
            lines.append(at(t, "api-03", api_request(rng)))
    return lines


def pod_oomkilled(rng: random.Random, start: datetime, end: datetime) -> list[Line]:
    """checkout-worker on k8s-node-03 is OOMKilled again and again; the kubelet back-off doubles up to 5 minutes."""
    lines: list[Line] = []
    t, restarts, backoff = start, 1, 10
    while t < end:
        killed = f"Container checkout-worker in pod {POD} OOMKilled (memory limit 512Mi), restart count {restarts}"
        lines.append((t, "WARN", "k8s-node-03", "kubelet", killed))
        message = f"Back-off restarting failed container checkout-worker in pod {POD}: CrashLoopBackOff, back-off {backoff}s"
        lines.append((t + timedelta(seconds=1), "WARN", "k8s-node-03", "kubelet", message))
        t += timedelta(seconds=backoff + rng.uniform(20, 40))  # back-off, then the container runs until it is killed again
        restarts, backoff = restarts + 1, min(2 * backoff, 300)
    return [line for line in lines if line[0] < end]


FAULTS = [
    Fault("disk_full", "log-02", ("log-02",), "runbook_disk_full.pdf", disk_full, silences=("log-02",)),
    Fault(
        "pg_primary_down",
        "db-primary-01",  # silent while down: only its effects on the replica and the API hosts are logged
        ("db-primary-01", "db-replica-02", "api-01", "api-02", "api-03"),
        "runbook_db_failover.docx",
        pg_primary_down,
        silences=("db-primary-01", "db-replica-02"),
    ),
    Fault("pg_replica_lag", "db-replica-02", ("db-replica-02",), "runbook_pg_replication_lag.html", pg_replica_lag, silences=("db-replica-02",)),
    Fault("cert_expiry", "ingress-01", ("ingress-01", "probe-eu-1"), "runbook_cert_expiry.md", cert_expiry, silences=("probe-eu-1",)),
    Fault("high_latency", "api-02", ("api-02",), "runbook_high_latency.txt", high_latency, silences=("api-02",)),
    Fault("api_error_rate", "api-03", ("api-03",), "runbook_api_error_rate.md", api_error_rate, silences=("api-03",)),
    Fault("pod_oomkilled", "k8s-node-03", ("k8s-node-03",), "runbook_pod_oomkilled.html", pod_oomkilled, silences=("k8s-node-03",)),
]


# --- decoys: one-off warnings that look like the start of a fault but are not ---

DECOYS: list[Callable[[datetime], list[Line]]] = [
    lambda t: [(t, "WARN", "api-01", "public-api", "POST /v2/orders 201 1450ms")],
    lambda t: [(t, "ERROR", "api-02", "public-api", "GET /v2/search 504 upstream timeout after 5000ms")],
    lambda t: [
        (t, "WARN", "probe-eu-1", "synthetics", "check https://api.example.com/healthz timeout after 5000ms, retrying"),
        (t + timedelta(seconds=5), "INFO", "probe-eu-1", "synthetics", "check https://api.example.com/healthz ok 131ms"),
    ],
    lambda t: [(t, "WARN", "db-replica-02", "pg-monitor", "replica lag 22 MB, replay_lag 3.1s")],
    lambda t: [
        (t, "WARN", "log-01", "disk-monitor", "/var/log usage 81%"),
        (t + timedelta(seconds=90), "INFO", "log-01", "logrotate", "rotated 14 files in /var/log/remote"),
        (t + timedelta(seconds=120), "INFO", "log-01", "disk-monitor", "/var/log usage 58%"),
    ],
    lambda t: [(t, "WARN", "k8s-node-03", "kubelet", f"Container checkout-worker in pod {POD} restarted (reason=Error, exitCode=1), restart 1")],
]


def schedule(rng: random.Random, start: datetime, end: datetime) -> list[tuple[Fault, datetime, datetime]]:
    """Every fault once, in random order, each inside its own time slot so no two overlap; the first 10% stays clean."""
    order = rng.sample(FAULTS, len(FAULTS))
    lead = (end - start) * 0.1
    slot = (end - start - lead) / len(order)
    windows = []
    for i, fault in enumerate(order):
        fault_start = (start + lead + slot * i + slot * rng.uniform(0, 0.3)).replace(microsecond=0)
        windows.append((fault, fault_start, (fault_start + slot * rng.uniform(0.3, 0.6)).replace(microsecond=0)))
    return windows


def decoy_time(rng: random.Random, start: datetime, end: datetime, windows: list[tuple[Fault, datetime, datetime]]) -> datetime:
    """A random time at least 5 minutes away from every fault, so a decoy is never part of one."""
    margin = timedelta(minutes=5)
    while True:
        t = start + (end - start - margin) * rng.random()
        if not any(fault_start - margin <= t < fault_end + margin for _, fault_start, fault_end in windows):
            return t


def generate(seed: int, start: datetime, hours: float) -> tuple[list[Line], list[dict[str, str | list[str]]]]:
    """All log lines in time order, and the ground-truth entry of every seeded fault."""
    rng = random.Random(seed)
    end = start + timedelta(hours=hours)
    windows = sorted(schedule(rng, start, end), key=lambda window: window[1])

    def silenced(host: str, t: datetime) -> bool:
        return any(host in fault.silences and fault_start <= t < fault_end for fault, fault_start, fault_end in windows)

    lines = [
        at(t, host, noise(rng))
        for host, (mean_seconds, noise) in HOSTS.items()
        for t in ticks(rng, start, end, mean_seconds)
        if not silenced(host, t)
    ]
    faults: list[dict[str, str | list[str]]] = []
    for number, (fault, fault_start, fault_end) in enumerate(windows, start=1):
        lines += fault.emit(rng, fault_start, fault_end)
        faults.append({
            "id": f"F{number}",
            "fault_type": fault.fault_type,
            "host": fault.host,
            "affected_hosts": list(fault.affected_hosts),
            "start": iso(fault_start),
            "end": iso(fault_end),
            "expected_runbook": fault.expected_runbook,
        })
    for i in range(int(hours * 2)):  # about two decoys an hour, each kind in turn
        lines += DECOYS[i % len(DECOYS)](decoy_time(rng, start, end, windows))
    return sorted(lines, key=lambda line: line[0]), faults


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--hours", type=float, default=4.0, help="length of the log, at least 2 (every fault needs its own slot)")
    default_start = datetime(2026, 11, 1, 6, tzinfo=UTC)  # fixed, not now(): same arguments → same files
    parser.add_argument("--start", type=datetime.fromisoformat, default=default_start, help="ISO 8601, e.g. 2026-11-01T06:00:00Z")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data" / "logs")
    args = parser.parse_args()
    if args.hours < 2:
        parser.error("--hours must be at least 2")

    start = args.start.astimezone(UTC) if args.start.tzinfo else args.start.replace(tzinfo=UTC)
    lines, faults = generate(args.seed, start, args.hours)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "site.log").write_text("".join(format_line(line) + "\n" for line in lines), encoding="utf-8", newline="\n")
    ground_truth = {"seed": args.seed, "start": iso(start), "end": iso(start + timedelta(hours=args.hours)), "log_file": "site.log", "faults": faults}
    (args.out_dir / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(lines)} lines with {len(faults)} faults to {args.out_dir / 'site.log'}, ground truth in ground_truth.json")


if __name__ == "__main__":
    main()
