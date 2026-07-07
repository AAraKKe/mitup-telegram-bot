import json
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from freezegun import freeze_time

from mitup_bot.exceptions import PatreonStateExpired, PatreonStateInvalid
from mitup_bot.patreon import oauth
from tests.helpers import create_patreon_config

TG_USER_ID = 997_601
MESSAGE_ID = 4242


def test_encode_decode_round_trip_returns_tg_user_id():
    config = create_patreon_config()
    state = oauth.encode_state(config, TG_USER_ID)

    decoded = oauth.decode_state(config, state)
    assert decoded.tg_user_id == TG_USER_ID
    # No message id was supplied, so the round-trip carries None.
    assert decoded.message_id is None


def test_encode_decode_round_trip_carries_message_id():
    config = create_patreon_config()
    state = oauth.encode_state(config, TG_USER_ID, MESSAGE_ID)

    decoded = oauth.decode_state(config, state)
    assert decoded.tg_user_id == TG_USER_ID
    assert decoded.message_id == MESSAGE_ID


def test_decode_legacy_state_without_message_id_key_defaults_to_none():
    # A state minted before message-id threading has no ``message_id`` key at all. Backward-compat:
    # it must decode cleanly with message_id=None rather than raising a KeyError.
    config = create_patreon_config()
    fernet = Fernet(config.state_secret.get_secret_value())
    legacy = fernet.encrypt(json.dumps({"tg_user_id": TG_USER_ID}).encode()).decode()

    decoded = oauth.decode_state(config, legacy)
    assert decoded.tg_user_id == TG_USER_ID
    assert decoded.message_id is None


def test_encode_produces_distinct_tokens_via_fernet_iv():
    # No nonce is stored: Fernet's random per-token IV already makes two states for the same user
    # distinct ciphertexts, and TTL is the replay bound.
    config = create_patreon_config()
    assert oauth.encode_state(config, TG_USER_ID) != oauth.encode_state(config, TG_USER_ID)


def test_decode_expired_state_raises_and_carries_age():
    config = create_patreon_config()
    with freeze_time("2026-07-05 12:00:00"):
        state = oauth.encode_state(config, TG_USER_ID)
    # Past the 3600s (1h) TTL: 71 minutes later.
    with freeze_time("2026-07-05 13:11:00"), pytest.raises(PatreonStateExpired) as excinfo:
        oauth.decode_state(config, state)
    # The token's embedded timestamp is authentic, so the age is measurable and roughly 71 minutes.
    assert excinfo.value.age_seconds == pytest.approx(71 * 60, abs=2)


def test_decode_just_under_ttl_still_succeeds():
    config = create_patreon_config()
    with freeze_time("2026-07-05 12:00:00"):
        state = oauth.encode_state(config, TG_USER_ID)
    # 59 minutes is still inside the 1h window.
    with freeze_time("2026-07-05 12:59:00"):
        assert oauth.decode_state(config, state).tg_user_id == TG_USER_ID


def test_scope_requests_only_base_identity():
    # identity alone returns the viewer's membership to our own campaign; identity.memberships would
    # only add other creators' pledges (unused) and a scarier consent screen.
    assert oauth.USER_SCOPES == "identity"


def test_state_ttl_is_one_hour():
    assert oauth.STATE_TTL_SECONDS == 3600


def test_decode_future_dated_state_reports_negative_age():
    # A token minted "ahead" of the validating clock is rejected as expired; its age is negative,
    # which is the clock-skew signal the callback surfaces (age below the TTL yet still rejected).
    config = create_patreon_config()
    with freeze_time("2026-07-05 12:10:00"):
        state = oauth.encode_state(config, TG_USER_ID)
    with freeze_time("2026-07-05 12:00:00"), pytest.raises(PatreonStateExpired) as excinfo:
        oauth.decode_state(config, state)
    assert excinfo.value.age_seconds == pytest.approx(-600, abs=2)
    assert excinfo.value.age_seconds < oauth.STATE_TTL_SECONDS


def test_decode_tampered_state_raises_invalid():
    config = create_patreon_config()
    state = oauth.encode_state(config, TG_USER_ID)
    tampered = state[:-4] + ("aaaa" if not state.endswith("aaaa") else "bbbb")
    with pytest.raises(PatreonStateInvalid):
        oauth.decode_state(config, tampered)


def test_decode_state_signed_with_other_key_raises_invalid():
    signing_config = create_patreon_config()
    other_config = create_patreon_config()
    state = oauth.encode_state(signing_config, TG_USER_ID)
    with pytest.raises(PatreonStateInvalid):
        oauth.decode_state(other_config, state)


def test_authorization_url_carries_oauth_parameters():
    config = create_patreon_config(client_id="cid", redirect_uri="https://bot.example/patreon/callback")
    url = oauth.authorization_url(config, TG_USER_ID)

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.patreon.com"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["cid"]
    assert query["redirect_uri"] == ["https://bot.example/patreon/callback"]
    assert query["scope"] == ["identity"]
    # The state round-trips back to the same user id.
    assert oauth.decode_state(config, query["state"][0]).tg_user_id == TG_USER_ID


def test_authorization_url_state_carries_message_id():
    config = create_patreon_config()
    url = oauth.authorization_url(config, TG_USER_ID, MESSAGE_ID)

    state = parse_qs(urlparse(url).query)["state"][0]
    decoded = oauth.decode_state(config, state)
    assert decoded.tg_user_id == TG_USER_ID
    assert decoded.message_id == MESSAGE_ID


def test_authorization_url_without_message_id_decodes_to_none():
    config = create_patreon_config()
    url = oauth.authorization_url(config, TG_USER_ID)

    state = parse_qs(urlparse(url).query)["state"][0]
    assert oauth.decode_state(config, state).message_id is None


def test_campaign_pledge_url_uses_campaign_id():
    config = create_patreon_config(campaign_id="98765")
    assert oauth.campaign_pledge_url(config) == "https://www.patreon.com/bePatron?c=98765"
