from .conftest import AUTH


def test_meta_tells_the_frontend_what_to_offer(client) -> None:
    body = client.get("/meta", headers=AUTH).json()

    assert body["model"] == "fake" and body["tickets_enabled"] is True
    assert body["range_presets"] == ["1h", "6h", "24h"]
    assert body["log_range"] == {"earliest": "2026-11-01T06:00:00Z", "latest": "2026-11-01T10:00:00Z"}  # bounds the time picker
