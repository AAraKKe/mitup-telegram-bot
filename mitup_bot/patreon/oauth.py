"""Fernet-signed OAuth ``state`` handling and Patreon URL construction.

The ``state`` parameter round-trips the initiating Telegram user id through Patreon's consent
screen. It is a Fernet token (keyed by ``PatreonConfig.state_secret``) carrying the ``tg_user_id``,
so the value is opaque and tamper-evident. Fernet stamps every token with a creation time, which the
``ttl`` check on the way back uses to reject buttons tapped long after they were rendered.

Replay is bounded by that TTL, not by a stored nonce: Fernet already gives every token a random 128-bit
IV, so two states for the same user are distinct ciphertexts, and re-linking is idempotent (it upserts
the same row), so replaying a still-valid state within the TTL window links the account the user
already meant to link. Persisting and consuming a single-use nonce would add a datastore round-trip for
no security gain over the TTL, so we deliberately don't.

These are pure functions over a ``PatreonConfig`` so they stay unit-testable without the runtime
config holder; callers resolve the live config from :mod:`mitup_bot.patreon` and pass it in.
"""

import datetime as dt
import json
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken

from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonStateExpired, PatreonStateInvalid

AUTHORIZE_URL = "https://www.patreon.com/oauth2/authorize"
# We only need the user's identity and their membership to the configured campaign, both of which
# base ``identity`` already returns via ``GET /identity?include=memberships`` (it always includes the
# viewer's membership to our own campaign). ``identity.memberships`` would only add the user's pledges
# to *other* creators — which we never read — at the cost of a scarier consent screen, so we omit it.
USER_SCOPES = "identity"
# The scopes the *creator* (campaign-owner) token must carry to manage member webhooks:
# ``w:campaigns.webhook`` authorizes the create/list/patch calls in the client, and ``identity``
# lets the token resolve its own campaign. This token is seeded out-of-band via CI (it is never
# minted through the user OAuth flow above), so nothing here consumes this constant — it is the
# codified record of what that seeded token needs to be granted.
CREATOR_SCOPES = "identity w:campaigns.webhook"
# The state is a Fernet token frozen into the Collaborate-menu button at render time, so this TTL is
# the clock from when the menu is drawn. A first-time Patreon login (email, 2FA, verification) routinely
# runs past ten minutes, so an hour tolerates a realistic sitting. The cost is negligible: re-linking is
# idempotent and the state only carries the initiator's own tg_user_id. Buttons older than the TTL still
# degrade gracefully to the friendly retry page, which re-renders a fresh state.
STATE_TTL_SECONDS = 3600


def encode_state(config: PatreonConfig, tg_user_id: int) -> str:
    """Build the opaque, tamper-evident ``state`` token carrying the initiating Telegram user id."""
    fernet = Fernet(config.state_secret.get_secret_value())
    payload = json.dumps({"tg_user_id": tg_user_id})
    return fernet.encrypt(payload.encode()).decode()


def decode_state(config: PatreonConfig, state: str, ttl: int = STATE_TTL_SECONDS) -> int:
    """Validate the ``state`` token and return the Telegram user id it carries.

    Signature validation and age are checked separately so the caller can distinguish an expired
    button (friendly "tap it again") from a genuinely invalid token: decrypting without a ttl
    proves authenticity, then a second decrypt with the ttl gates on age. On expiry the token's
    embedded timestamp is authentic, so the age is measured and carried on the exception — that lets
    the callback tell slow consent from clock skew from a stale button.
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
        minted_at = dt.datetime.fromtimestamp(fernet.extract_timestamp(token), tz=dt.UTC)
        age_seconds = (dt.datetime.now(dt.UTC) - minted_at).total_seconds()
        raise PatreonStateExpired(age_seconds=age_seconds) from error

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
