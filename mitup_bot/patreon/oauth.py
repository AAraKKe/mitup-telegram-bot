"""Fernet-signed OAuth ``state`` handling and Patreon URL construction.

The ``state`` parameter round-trips the initiating Telegram user id through Patreon's consent
screen. It is a Fernet token (keyed by ``PatreonConfig.state_secret``) carrying the ``tg_user_id``,
so the value is opaque and tamper-evident. Fernet stamps every token with a creation time, which the
``ttl`` check on the way back uses to reject buttons tapped long after they were rendered.

Replay is bounded by that TTL, not by a stored nonce: Fernet already gives every token a random 128-bit
IV, so two states for the same user are distinct ciphertexts, and re-linking is idempotent (it upserts
the same row), so replaying a still-valid state within the 10-minute window links the account the user
already meant to link. Persisting and consuming a single-use nonce would add a datastore round-trip for
no security gain over the TTL, so we deliberately don't.

These are pure functions over a ``PatreonConfig`` so they stay unit-testable without the runtime
config holder; callers resolve the live config from :mod:`mitup_bot.patreon` and pass it in.
"""

import json
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken

from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonStateExpired, PatreonStateInvalid

AUTHORIZE_URL = "https://www.patreon.com/oauth2/authorize"
# The user-facing flow only needs to read the user's identity and their memberships to decide
# whether they are an active patron of the configured campaign.
USER_SCOPES = "identity identity.memberships"
# The inline button can sit in a chat for days, so the round-trip is validated against this TTL
# rather than trusting that the redirect happens promptly.
STATE_TTL_SECONDS = 600


def encode_state(config: PatreonConfig, tg_user_id: int) -> str:
    """Build the opaque, tamper-evident ``state`` token carrying the initiating Telegram user id."""
    fernet = Fernet(config.state_secret.get_secret_value())
    payload = json.dumps({"tg_user_id": tg_user_id})
    return fernet.encrypt(payload.encode()).decode()


def decode_state(config: PatreonConfig, state: str, ttl: int = STATE_TTL_SECONDS) -> int:
    """Validate the ``state`` token and return the Telegram user id it carries.

    Signature validation and age are checked separately so the caller can distinguish an expired
    button (friendly "tap it again") from a genuinely invalid token: decrypting without a ttl
    proves authenticity, then a second decrypt with the ttl gates on age.
    """
    fernet = Fernet(config.state_secret.get_secret_value())
    token = state.encode()
    try:
        raw = fernet.decrypt(token)
    except InvalidToken as error:
        raise PatreonStateInvalid from error

    try:
        fernet.decrypt(token, ttl=ttl)
    except InvalidToken as error:
        raise PatreonStateExpired from error

    payload = json.loads(raw)
    return int(payload["tg_user_id"])


def authorization_url(config: PatreonConfig, tg_user_id: int) -> str:
    """Build the Patreon consent URL the user is sent to, embedding a fresh signed ``state``."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": USER_SCOPES,
            "state": encode_state(config, tg_user_id),
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def campaign_pledge_url(config: PatreonConfig) -> str:
    """Public "become a patron" link for the configured campaign, derivable from the campaign id."""
    return f"https://www.patreon.com/bePatron?c={config.campaign_id}"
