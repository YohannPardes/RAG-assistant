from datetime import timedelta

from .conftest import AUTH, RANGE, SINCE


def test_windows_previews_the_prefilter_without_a_run(client, job, previewed, window) -> None:
    response = client.get("/windows", params=RANGE, headers=AUTH)

    [summary] = response.json()
    assert summary["window_id"] == window.id and summary["rule"] == "db_leader_unreachable"
    assert summary["events"] == len(window.events) and "raw" not in str(summary)  # counts, not log text
    assert previewed == [(SINCE, SINCE + timedelta(hours=4))]
    assert job.requests == []  # no run, no model


def test_windows_refuses_a_bad_range(client, previewed) -> None:
    reversed_range = client.get("/windows", params={"since": RANGE["until"], "until": RANGE["since"]}, headers=AUTH)
    naive = client.get("/windows", params={"since": "2026-11-01T06:00:00", "until": "2026-11-01T10:00:00"}, headers=AUTH)

    assert (reversed_range.status_code, reversed_range.json()["code"]) == (422, "invalid_request")
    assert (naive.status_code, naive.json()["code"]) == (422, "invalid_request")
    assert previewed == []
