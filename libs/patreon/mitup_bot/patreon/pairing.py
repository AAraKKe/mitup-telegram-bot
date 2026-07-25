"""Pairing codes that bind a completed Patreon consent to a Telegram account.

The OAuth leg (see :mod:`mitup_bot.patreon.oauth`) proves *which Patreon account* consented, but it
runs in a browser and can prove nothing about who is holding the phone. So the callback grants
nothing: it mints a pairing code, stores only its hash, and renders the code in the browser that
completed the consent. Whoever holds that code finishes the link by sending it to the bot from their
own Telegram account, and *that* message is what names the account the Patreon identity lands on.

The code therefore has to survive a round trip through a Telegram deep link, whose ``start`` payload
Telegram restricts to base64url characters (``A-Z a-z 0-9 - _``) and 64 characters. ``token_urlsafe``
emits exactly that alphabet, and the prefixed payload stays well inside the limit.

Only the SHA-256 hash is ever persisted. The code is high-entropy and random, so a plain hash (no
salt, no KDF) is enough to make the stored value useless for reconstructing it, while keeping lookup
a single indexed equality match.
"""

import hashlib
import secrets

# Byte length of the raw pairing code. token_urlsafe(24) renders 32 base64url characters, which has
# to fit two separate 64-byte budgets: Telegram's `start` payload (with the deep-link prefix, 45)
# and the confirm button's callback data (with its own prefix, 52). 24 bytes is 192 bits of entropy,
# far past anything guessable.
PAIRING_CODE_BYTES = 24
# How long a pairing code stays redeemable, covering the whole browser to Telegram to confirm dwell
# time. The user is looking at the code in their browser with the button right there, so the window
# only has to cover switching apps and reading one prompt, not a whole login.
PAIRING_CODE_TTL_SECONDS = 900
# Deep-link payload prefix identifying a pairing-code redemption. Telegram forbids `:` in a `start`
# payload, so `_` separates the prefix from the code; the code's own `-`/`_` characters stay
# unambiguous because parsing splits on the first separator only.
PAIRING_DEEP_LINK_PREFIX = "patreonlink"


def generate_pairing_code() -> str:
    """Mint a fresh, unguessable pairing code in Telegram's ``start``-payload alphabet."""
    return secrets.token_urlsafe(PAIRING_CODE_BYTES)


def hash_pairing_code(code: str) -> str:
    """The stored form of a pairing code: hex SHA-256, so a database read never yields the code."""
    return hashlib.sha256(code.encode()).hexdigest()


def pairing_deep_link(bot_username: str, code: str) -> str:
    """The ``t.me`` link that sends ``/start`` with the pairing code from the user's own account."""
    return f"https://t.me/{bot_username}?start={PAIRING_DEEP_LINK_PREFIX}_{code}"
