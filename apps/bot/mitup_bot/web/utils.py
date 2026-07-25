import secrets


def secret_header_matches(received: str | None, expected: str | None) -> bool:
    """Constant-time check that a request header carries the value it must equal.

    Compares bytes rather than str: the ASGI layer decodes header values as latin-1, so a
    byte >= 0x80 anywhere in the header yields a non-ASCII str, and ``compare_digest``
    raises ``TypeError`` on those instead of returning False. Encoding back with latin-1
    recovers the exact bytes the client sent, so any header value can be rejected rather
    than escaping as a 500. A missing value on either side fails closed.
    """
    if received is None or expected is None:
        return False
    return secrets.compare_digest(received.encode("latin-1"), expected.encode("utf-8"))
