"""Signed ticket drafts (agent/15): how a chat-drafted ticket reaches POST /tickets without the agent keeping state.

The token carries the whole TicketCreate the code built (agent/10: evidence text, window times, runbook reference) and
an expiry, signed with HMAC-SHA256. The browser can show it but can't change it: any edit breaks the signature.
"""

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from siteops_common.contracts import TicketCreate


class DraftTokenError(ValueError):
    """The token was not signed by this agent, was altered, or has expired."""


class DraftSigner:
    def __init__(self, secret: bytes, ttl: timedelta, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        if len(secret) < 32:
            raise ValueError("the draft secret must be at least 32 bytes")
        self._secret = secret
        self.ttl = ttl
        self._now = now

    def sign(self, ticket: TicketCreate) -> str:
        """`<payload>.<signature>`, both base64url: the payload is the ticket and its expiry, as JSON."""
        expires = (self._now() + self.ttl).timestamp()
        payload = _encode(json.dumps({"ticket": ticket.model_dump(mode="json"), "exp": expires}, separators=(",", ":")).encode())
        return f"{payload}.{_encode(self._mac(payload))}"

    def verify(self, token: str) -> TicketCreate:
        """The ticket the token was signed with. Raises DraftTokenError if it is forged, altered or expired."""
        payload, _, signature = token.partition(".")
        try:
            given = _decode(signature)
        except ValueError as error:
            raise DraftTokenError("malformed draft token") from error
        if not signature or not hmac.compare_digest(given, self._mac(payload)):  # constant time, as for API keys
            raise DraftTokenError("the draft token's signature doesn't match")
        try:
            data = json.loads(_decode(payload))
            ticket = TicketCreate.model_validate(data["ticket"])
            expires = float(data["exp"])
        except (ValueError, KeyError, TypeError, ValidationError) as error:  # signed by us, so only a code change gets here
            raise DraftTokenError("unreadable draft token") from error
        if self._now().timestamp() >= expires:
            raise DraftTokenError("the draft has expired: write the ticket again")
        return ticket

    def _mac(self, payload: str) -> bytes:
        return hmac.new(self._secret, payload.encode(), hashlib.sha256).digest()


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
