from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.engine.default import DefaultDialect

from mitup_bot.exceptions import TokenEncryptionNotConfigured
from mitup_bot.models.premium import EncryptedToken, TokenCipher, configure_token_encryption


@pytest.fixture(autouse=True)
def reset_token_cipher() -> Iterator[None]:
    """Isolate the process-wide Fernet: every test starts unconfigured and restores after."""
    saved = TokenCipher.fernet
    TokenCipher.fernet = None
    try:
        yield
    finally:
        TokenCipher.fernet = saved


@pytest.fixture
def configured_key() -> str:
    key = Fernet.generate_key().decode()
    configure_token_encryption(key)
    return key


def test_encrypt_then_decrypt_round_trips(configured_key: str):
    token = "patreon-user-access-token"

    ciphertext = TokenCipher.encrypt(token)

    assert ciphertext != token
    assert TokenCipher.decrypt(ciphertext) == token


def test_ciphertext_does_not_leak_plaintext(configured_key: str):
    token = "super-secret-refresh-token"

    ciphertext = TokenCipher.encrypt(token)

    assert token not in ciphertext


def test_encrypt_raises_when_unconfigured():
    with pytest.raises(TokenEncryptionNotConfigured):
        TokenCipher.encrypt("anything")


def test_decrypt_raises_when_unconfigured():
    with pytest.raises(TokenEncryptionNotConfigured):
        TokenCipher.decrypt("anything")


def test_a_different_key_cannot_decrypt(configured_key: str):
    ciphertext = TokenCipher.encrypt("token")

    configure_token_encryption(Fernet.generate_key().decode())

    with pytest.raises(InvalidToken):
        TokenCipher.decrypt(ciphertext)


def test_type_decorator_binds_and_reads_round_trip(configured_key: str):
    decorator = EncryptedToken()
    dialect = DefaultDialect()

    bound = decorator.process_bind_param("access-token", dialect)

    assert bound is not None
    assert bound != "access-token"
    assert decorator.process_result_value(bound, dialect) == "access-token"


def test_type_decorator_passes_none_through_without_configuration():
    decorator = EncryptedToken()
    dialect = DefaultDialect()

    assert decorator.process_bind_param(None, dialect) is None
    assert decorator.process_result_value(None, dialect) is None
