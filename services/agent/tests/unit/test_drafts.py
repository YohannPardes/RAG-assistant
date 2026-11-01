import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from siteops_agent.drafts import DraftSigner, DraftTokenError

from .api.conftest import SECRET, TICKET

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def signer(now: datetime = NOW, secret: bytes = SECRET) -> DraftSigner:
    return DraftSigner(secret, ttl=timedelta(minutes=30), now=lambda: now)


def test_a_signed_ticket_comes_back_unchanged() -> None:
    assert signer().verify(signer().sign(TICKET)) == TICKET


def test_the_token_is_readable_but_any_edit_breaks_it() -> None:
    # the payload is plain base64 JSON (no secret in it), so the browser could edit it: the signature catches that
    payload, signature = signer().sign(TICKET).split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["ticket"]["evidence"] = ["a line that was never logged"]
    edited = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    with pytest.raises(DraftTokenError, match="signature"):
        signer().verify(f"{edited}.{signature}")


def test_a_token_signed_with_another_secret_is_refused() -> None:
    with pytest.raises(DraftTokenError, match="signature"):
        signer().verify(signer(secret=b"o" * 32).sign(TICKET))


@pytest.mark.parametrize("token", ["", "no-dot", "a.b", "....", "abc.%%%"])
def test_a_malformed_token_is_refused(token) -> None:
    with pytest.raises(DraftTokenError):
        signer().verify(token)


def test_a_token_expires_after_its_ttl() -> None:
    token = signer().sign(TICKET)

    assert signer(now=NOW + timedelta(minutes=29)).verify(token) == TICKET
    with pytest.raises(DraftTokenError, match="expired"):
        signer(now=NOW + timedelta(minutes=30)).verify(token)


def test_a_short_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        DraftSigner(b"short", ttl=timedelta(minutes=30))
