"""Fernet-signed OAuth ``state`` handling and Patreon URL construction.

The OAuth leg is anonymous. ``state`` is a Fernet token (keyed by ``PatreonConfig.state_secret``)
wrapping nothing but a random nonce, so the value proves the redirect came from us and has not been
tampered with, and nothing else. Whose Telegram account the consent belongs to is decided afterwards,
inside Telegram, by redeeming a pairing code (see :mod:`mitup_bot.patreon.pairing`).

Keeping identity out of ``state`` is what makes the consent URL safe to be seen by anyone: the token
travels through a browser, so anybody holding it can complete the consent — but completing it grants
them nothing beyond a pairing code rendered in their own browser.

Fernet stamps every token with a creation time, which the ``ttl`` check on the way back uses to reject
a consent screen that was left sitting far longer than the flow needs.

These are pure functions over a ``PatreonConfig`` so they stay unit-testable without the runtime
config holder; callers resolve the live config from :mod:`mitup_bot.patreon` and pass it in.
"""

import datetime as dt
import json
import secrets
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
# The state is frozen into the Collaborate-menu button at render time, so this TTL is the clock from
# when the menu is drawn. It bounds how long a consent screen stays completable, which keeps a
# harvested consent URL from being useful much later. A first-time Patreon login (email, 2FA) fits
# comfortably inside it, and an expired button degrades to the friendly retry page, which re-renders
# a fresh state.
STATE_TTL_SECONDS = 900
# Byte length of the ``state`` nonce. Fernet already randomizes every ciphertext through its IV; the
# explicit nonce makes the payload's only job — being unguessable and meaningless — self-evident.
STATE_NONCE_BYTES = 16


def encode_state(config: PatreonConfig) -> str:
    """Build the opaque, tamper-evident ``state`` token: a random nonce and nothing else."""
    fernet = Fernet(config.state_secret.get_secret_value())
    payload = json.dumps({"nonce": secrets.token_urlsafe(STATE_NONCE_BYTES)})
    return fernet.encrypt(payload.encode()).decode()


def validate_state(config: PatreonConfig, state: str, ttl: int = STATE_TTL_SECONDS) -> None:
    """Check that ``state`` is one of ours and still fresh, raising when it is not.

    Signature validation and age are checked separately so the caller can distinguish an expired
    button (friendly "tap it again") from a genuinely invalid token: decrypting without a ttl
    proves authenticity, then a second decrypt with the ttl gates on age. On expiry the token's
    embedded timestamp is authentic, so the age is measured and carried on the exception — that lets
    the callback tell slow consent from clock skew from a stale button.
    """
    fernet = Fernet(config.state_secret.get_secret_value())
    token = state.encode()
    try:
        fernet.decrypt(token)
    except InvalidToken as error:
        raise PatreonStateInvalid from error

    try:
        fernet.decrypt(token, ttl=ttl)
    except InvalidToken as error:
        minted_at = dt.datetime.fromtimestamp(fernet.extract_timestamp(token), tz=dt.UTC)
        age_seconds = (dt.datetime.now(dt.UTC) - minted_at).total_seconds()
        raise PatreonStateExpired(age_seconds=age_seconds) from error


def authorization_url(config: PatreonConfig) -> str:
    """Build the Patreon consent URL the user is sent to, embedding a fresh anonymous ``state``."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": USER_SCOPES,
            "state": encode_state(config),
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def campaign_pledge_url(config: PatreonConfig) -> str:
    """Public "become a patron" link for the configured campaign, derivable from the campaign id."""
    return f"https://www.patreon.com/bePatron?c={config.campaign_id}"
