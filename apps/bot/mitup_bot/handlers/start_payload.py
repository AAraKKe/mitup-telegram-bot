"""Parsing the payload a Telegram deep link attaches to ``/start``.

Opening ``https://t.me/<bot>?start=<payload>`` makes Telegram send ``/start <payload>`` from the
user's own account, which is how every deep link into the bot arrives. Telegram restricts that
payload to base64url characters (``A-Z a-z 0-9 - _``) and 64 of them, so a ``:`` between the kind of
link and its token is not available: ``_`` separates them instead.

Because ``_`` is also legal inside a token, the split takes the first separator only — that keeps the
kind unambiguous no matter what the token contains.
"""

from collections.abc import Sequence
from dataclasses import dataclass

# Deep link that drops the user straight into meeting creation, used by the inline-query surface.
INLINE_KIND = "inline"


@dataclass(frozen=True)
class StartPayload:
    """A parsed ``/start`` payload: what kind of link it is, and the token it carries if any."""

    kind: str
    token: str | None


def parse_start_payload(args: Sequence[str] | None) -> StartPayload | None:
    """Read the deep-link payload out of a ``/start`` command's arguments.

    Returns None for a bare ``/start``. A payload with no separator (or an empty token after one)
    yields ``token=None``, so a kind-only link and a truncated one are the same shape and each caller
    decides whether it needs a token.
    """
    if not args:
        return None
    kind, _, token = args[0].partition("_")
    return StartPayload(kind=kind, token=token or None)
