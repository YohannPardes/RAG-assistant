"""SQLite ticket store with fingerprint deduplication (agent/3)."""

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from siteops_common.contracts import CreateTicketResult, Ticket, TicketCreate, fingerprint

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id            TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    created_at_us INTEGER NOT NULL,  -- microseconds since the epoch (UTC): compares as a number, not as text
    body          TEXT NOT NULL      -- the Ticket as JSON, exactly what create_ticket returned
);
CREATE INDEX IF NOT EXISTS tickets_by_fingerprint ON tickets (fingerprint, created_at_us);
"""


def _microseconds(time: datetime) -> int:
    return (time - _EPOCH) // timedelta(microseconds=1)


class TicketStore:
    """Stores tickets; one with the fingerprint of a ticket created less than `dedup_window` ago is not stored again."""

    def __init__(
        self,
        path: Path | str,
        dedup_window: timedelta,
        lock_timeout: float,  # seconds a write waits while another one holds the database lock
        now: Callable[[], datetime] = lambda: datetime.now(UTC),  # tests pass a fake clock
    ) -> None:
        self.path = str(path)
        self.dedup_window = dedup_window
        self.lock_timeout = lock_timeout
        self._now = now
        with closing(self._connect()) as db:
            db.executescript(_SCHEMA)

    def create(self, request: TicketCreate) -> CreateTicketResult:
        """The new ticket (created=True), or the recent one with the same fingerprint (created=False)."""
        key = fingerprint(request.fault_type, request.host)
        now = self._now()
        with closing(self._connect()) as db, db:  # `with db`: commit on success, rollback on error
            # take the write lock before reading: two identical tickets sent at once can't both be new
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM tickets WHERE fingerprint = ? AND created_at_us > ? ORDER BY created_at_us DESC LIMIT 1",
                (key, _microseconds(now - self.dedup_window)),
            ).fetchone()
            if row is not None:
                return CreateTicketResult(ticket=Ticket.model_validate_json(row[0]), created=False)

            ticket = Ticket.model_validate(request.model_dump() | {"id": str(uuid.uuid4()), "created_at": now, "fingerprint": key})
            db.execute("INSERT INTO tickets VALUES (?, ?, ?, ?)", (ticket.id, key, _microseconds(now), ticket.model_dump_json()))
            return CreateTicketResult(ticket=ticket, created=True)

    def _connect(self) -> sqlite3.Connection:
        # one connection per call: safe from any thread; transactions are explicit (BEGIN above)
        return sqlite3.connect(self.path, isolation_level=None, timeout=self.lock_timeout)
