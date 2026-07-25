import hashlib
import re
from urllib.parse import parse_qs, urlparse

from mitup_bot.patreon import pairing

# Telegram's documented `start` payload alphabet and length cap. A code that violates either would
# either be rejected or silently truncated by Telegram, breaking redemption for everyone.
START_PAYLOAD_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def test_generated_codes_are_unique():
    codes = {pairing.generate_pairing_code() for _ in range(100)}
    assert len(codes) == 100


def test_generated_code_fits_telegrams_start_payload_rules():
    payload = f"{pairing.PAIRING_DEEP_LINK_PREFIX}_{pairing.generate_pairing_code()}"
    assert START_PAYLOAD_PATTERN.match(payload)


def test_hash_is_sha256_of_the_code():
    code = pairing.generate_pairing_code()
    assert pairing.hash_pairing_code(code) == hashlib.sha256(code.encode()).hexdigest()


def test_hash_does_not_contain_the_code():
    code = pairing.generate_pairing_code()
    assert code not in pairing.hash_pairing_code(code)


def test_hash_is_stable_and_distinct_per_code():
    first, second = pairing.generate_pairing_code(), pairing.generate_pairing_code()
    assert pairing.hash_pairing_code(first) == pairing.hash_pairing_code(first)
    assert pairing.hash_pairing_code(first) != pairing.hash_pairing_code(second)


def test_deep_link_targets_the_bot_with_a_start_payload():
    code = pairing.generate_pairing_code()
    url = pairing.pairing_deep_link("mitup_bot", code)

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "t.me"
    assert parsed.path == "/mitup_bot"
    assert parse_qs(parsed.query)["start"] == [f"{pairing.PAIRING_DEEP_LINK_PREFIX}_{code}"]


def test_deep_link_separator_is_an_underscore_not_a_colon():
    # A colon is not a legal `start` payload character, so the prefix must be joined with `_`.
    url = pairing.pairing_deep_link("mitup_bot", pairing.generate_pairing_code())
    assert ":" not in parse_qs(urlparse(url).query)["start"][0]
