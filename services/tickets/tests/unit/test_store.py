import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from siteops_tickets.store import TicketStore

T0 = datetime(2026, 11, 1, 7, 15, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path, clock) -> TicketStore:
    return TicketStore(tmp_path / "tickets.db", dedup_window=timedelta(minutes=60), lock_timeout=10.0, now=clock)


def stored(store: TicketStore) -> int:
    with sqlite3.connect(store.path) as db:
        return int(db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])


def test_a_new_ticket_is_stored_with_what_the_service_adds(store, ticket) -> None:
    result = store.create(ticket)

    assert result.created
    assert result.ticket.id and result.ticket.created_at == T0
    assert result.ticket.fingerprint == "disk_full|log-02"
    assert result.ticket.suggested_fix == ticket.suggested_fix  # what the agent sent is kept as is
    assert stored(store) == 1


def test_the_same_fault_on_the_same_host_within_the_window_is_not_stored_again(store, clock, ticket) -> None:
    first = store.create(ticket)
    clock.now = T0 + timedelta(minutes=59)

    again = store.create(ticket.model_copy(update={"title": "another title, same fault"}))

    assert not again.created
    assert again.ticket == first.ticket  # the existing ticket comes back, unchanged
    assert stored(store) == 1


def test_the_host_case_and_spaces_do_not_make_a_new_fault(store, ticket) -> None:
    store.create(ticket)

    assert not store.create(ticket.model_copy(update={"host": " LOG-02 "})).created


@pytest.mark.parametrize("change", [{"host": "log-01"}, {"fault_type": "high_latency"}], ids=["other-host", "other-fault-type"])
def test_another_host_or_fault_type_is_a_new_ticket(store, ticket, change) -> None:
    store.create(ticket)

    assert store.create(ticket.model_copy(update=change)).created
    assert stored(store) == 2


def test_after_the_window_the_same_fault_is_a_new_ticket(store, clock, ticket) -> None:
    store.create(ticket)
    clock.now = T0 + timedelta(minutes=60)  # the window is "less than 60 minutes ago"

    assert store.create(ticket).created


def test_dedup_survives_a_restart(tmp_path, clock, ticket) -> None:
    TicketStore(tmp_path / "tickets.db", dedup_window=timedelta(minutes=60), lock_timeout=10.0, now=clock).create(ticket)

    restarted = TicketStore(tmp_path / "tickets.db", dedup_window=timedelta(minutes=60), lock_timeout=10.0, now=clock)

    assert not restarted.create(ticket).created


def test_identical_tickets_sent_at_once_are_stored_once(store, ticket) -> None:
    # a retried call racing the original one must not make two tickets
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.create(ticket), range(16)))

    assert sum(result.created for result in results) == 1
    assert len({result.ticket.id for result in results}) == 1
    assert stored(store) == 1
