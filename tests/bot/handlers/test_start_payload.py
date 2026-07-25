import pytest

from mitup_bot.handlers.start_payload import INLINE_KIND, StartPayload, parse_start_payload
from mitup_bot.patreon.pairing import PAIRING_DEEP_LINK_PREFIX


def test_bare_start_has_no_payload():
    assert parse_start_payload(None) is None
    assert parse_start_payload([]) is None


def test_inline_sentinel_parses_as_a_kind_without_a_token():
    assert parse_start_payload(["inline"]) == StartPayload(kind=INLINE_KIND, token=None)


def test_pairing_payload_splits_prefix_from_code():
    payload = parse_start_payload([f"{PAIRING_DEEP_LINK_PREFIX}_abc123"])
    assert payload == StartPayload(kind=PAIRING_DEEP_LINK_PREFIX, token="abc123")


@pytest.mark.parametrize("token", ["a_b_c", "-_-", "x-y_z", "___"])
def test_token_may_contain_the_separator(token: str):
    # Splitting on the first separator only is what keeps the kind unambiguous, since the
    # base64url code alphabet includes `_` itself.
    payload = parse_start_payload([f"{PAIRING_DEEP_LINK_PREFIX}_{token}"])
    assert payload == StartPayload(kind=PAIRING_DEEP_LINK_PREFIX, token=token)


def test_truncated_payload_yields_no_token():
    assert parse_start_payload([f"{PAIRING_DEEP_LINK_PREFIX}_"]) == StartPayload(
        kind=PAIRING_DEEP_LINK_PREFIX, token=None
    )


def test_unknown_kind_parses_without_raising():
    # An unrecognized deep link is the caller's problem to route, not a parse error.
    assert parse_start_payload(["somethingelse_tok"]) == StartPayload(kind="somethingelse", token="tok")


def test_extra_arguments_are_ignored():
    # Telegram only ever sends one payload; anything after it is not part of the deep link.
    assert parse_start_payload(["inline", "junk"]) == StartPayload(kind=INLINE_KIND, token=None)
