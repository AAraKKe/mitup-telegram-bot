import json
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from freezegun import freeze_time

from mitup_bot.exceptions import PatreonStateExpired, PatreonStateInvalid
from mitup_bot.patreon import oauth
from tests.helpers import create_patreon_config


def test_state_carries_no_telegram_identity():
    # The security property of the whole flow: the token that travels through the browser says
    # nothing about who started it, so handing the consent URL to somebody else transfers no claim
    # on anyone's Mitup account.
    config = create_patreon_config()
    state = oauth.encode_state(config)

    payload = json.loads(Fernet(config.state_secret.get_secret_value()).decrypt(state.encode()))
    assert set(payload) == {"nonce"}


def test_validate_accepts_a_freshly_encoded_state():
    config = create_patreon_config()
    oauth.validate_state(config, oauth.encode_state(config))


def test_encode_produces_distinct_tokens():
    config = create_patreon_config()
    assert oauth.encode_state(config) != oauth.encode_state(config)


def test_validate_expired_state_raises_and_carries_age():
    config = create_patreon_config()
    with freeze_time("2026-07-05 12:00:00"):
        state = oauth.encode_state(config)
    # Past the 900s TTL: 20 minutes later.
    with freeze_time("2026-07-05 12:20:00"), pytest.raises(PatreonStateExpired) as excinfo:
        oauth.validate_state(config, state)
    # The token's embedded timestamp is authentic, so the age is measurable and roughly 20 minutes.
    assert excinfo.value.age_seconds == pytest.approx(20 * 60, abs=2)


def test_validate_just_under_ttl_still_succeeds():
    config = create_patreon_config()
    with freeze_time("2026-07-05 12:00:00"):
        state = oauth.encode_state(config)
    # 14 minutes is still inside the 15-minute window.
    with freeze_time("2026-07-05 12:14:00"):
        oauth.validate_state(config, state)


def test_scope_requests_only_base_identity():
    # identity alone returns the viewer's membership to our own campaign; identity.memberships would
    # only add other creators' pledges (unused) and a scarier consent screen.
    assert oauth.USER_SCOPES == "identity"


def test_state_ttl_is_fifteen_minutes():
    assert oauth.STATE_TTL_SECONDS == 900


def test_validate_future_dated_state_reports_negative_age():
    # A token minted "ahead" of the validating clock is rejected as expired; its age is negative,
    # which is the clock-skew signal the callback surfaces (age below the TTL yet still rejected).
    config = create_patreon_config()
    with freeze_time("2026-07-05 12:10:00"):
        state = oauth.encode_state(config)
    with freeze_time("2026-07-05 12:00:00"), pytest.raises(PatreonStateExpired) as excinfo:
        oauth.validate_state(config, state)
    assert excinfo.value.age_seconds == pytest.approx(-600, abs=2)
    assert excinfo.value.age_seconds < oauth.STATE_TTL_SECONDS


def test_validate_tampered_state_raises_invalid():
    config = create_patreon_config()
    state = oauth.encode_state(config)
    tampered = state[:-4] + ("aaaa" if not state.endswith("aaaa") else "bbbb")
    with pytest.raises(PatreonStateInvalid):
        oauth.validate_state(config, tampered)


def test_validate_state_signed_with_other_key_raises_invalid():
    signing_config = create_patreon_config()
    other_config = create_patreon_config()
    state = oauth.encode_state(signing_config)
    with pytest.raises(PatreonStateInvalid):
        oauth.validate_state(other_config, state)


def test_authorization_url_carries_oauth_parameters():
    config = create_patreon_config(client_id="cid", redirect_uri="https://bot.example/patreon/callback")
    url = oauth.authorization_url(config)

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.patreon.com"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["cid"]
    assert query["redirect_uri"] == ["https://bot.example/patreon/callback"]
    assert query["scope"] == ["identity"]
    oauth.validate_state(config, query["state"][0])


def test_authorization_url_is_identical_for_every_caller_apart_from_the_state():
    # Two users tapping Link Patreon account get URLs that differ only by the random state, so the
    # URL itself cannot be attributed to whoever requested it.
    config = create_patreon_config()
    first = parse_qs(urlparse(oauth.authorization_url(config)).query)
    second = parse_qs(urlparse(oauth.authorization_url(config)).query)

    assert first["state"] != second["state"]
    assert {key: value for key, value in first.items() if key != "state"} == {
        key: value for key, value in second.items() if key != "state"
    }


def test_campaign_pledge_url_uses_campaign_id():
    config = create_patreon_config(campaign_id="98765")
    assert oauth.campaign_pledge_url(config) == "https://www.patreon.com/bePatron?c=98765"
