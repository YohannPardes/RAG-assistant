"""What every route shares: the key check, /health without it, and one error shape."""

import pytest

from .conftest import API_KEY, AUTH, RANGE

ROUTES = [
    ("post", "/chat"),
    ("post", "/tickets"),
    ("get", "/meta"),
    ("post", "/triage"),
    ("get", "/triage"),
    ("get", "/triage/x"),
    ("get", "/windows"),
]


def test_health_needs_no_key(client) -> None:
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers", [{}, {"authorization": "Bearer wrong-key-0123456789"}, {"authorization": API_KEY}], ids=["none", "wrong", "no-bearer"]
)
@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_other_route_needs_the_key(client, job, engine, sink, headers, method, path) -> None:
    response = client.request(method, path, json=RANGE, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "missing or invalid API key", "code": "unauthorized"}
    assert job.requests == [] and engine.requests == [] and sink.tickets == []  # refused before any work


def test_an_invalid_request_names_the_field(client) -> None:
    response = client.post("/chat", json={"messages": [], **RANGE}, headers=AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert "messages" in response.json()["detail"]  # shown next to the input


def test_an_unknown_route_has_the_same_error_shape(client) -> None:
    response = client.get("/nope", headers=AUTH)

    assert response.status_code == 404 and set(response.json()) == {"detail", "code"}
