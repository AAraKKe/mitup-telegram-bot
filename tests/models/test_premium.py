from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.engine.default import DefaultDialect

from mitup_bot.exceptions import TokenEncryptionNotConfigured
from mitup_bot.models.subscriptions import EncryptedToken, TokenCipher, configure_token_encryption


@pytest.fixture(autouse=True)
def reset_token_cipher() -> Iterator[None]:
    """Isolate the process-wide cipher: every test starts unconfigured and restores after."""
    saved = TokenCipher.cipher
    TokenCipher.cipher = None
    try:
        yield
    finally:
        TokenCipher.cipher = saved


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


def test_configure_requires_at_least_one_key():
    with pytest.raises(ValueError, match="requires at least one Fernet key"):
        configure_token_encryption()


def test_legacy_key_decrypts_while_primary_key_encrypts():
    """Rotation semantics: `(new, old)` reads old ciphertext but writes under the new key."""
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    configure_token_encryption(old_key)
    legacy_ciphertext = TokenCipher.encrypt("token")

    # Rotate: new key is primary (writes), old key stays for reads.
    configure_token_encryption(new_key, old_key)

    # Ciphertext written under the old key still decrypts after rotation.
    assert TokenCipher.decrypt(legacy_ciphertext) == "token"

    # A fresh write is encrypted under the new primary key, not the legacy one.
    rotated_ciphertext = TokenCipher.encrypt("token")
    assert Fernet(new_key).decrypt(rotated_ciphertext.encode()).decode() == "token"
    with pytest.raises(InvalidToken):
        Fernet(old_key).decrypt(rotated_ciphertext.encode())


def test_multiple_legacy_keys_decrypt_and_primary_key_encrypts():
    """Every legacy key in the list decrypts, not just the one MultiFernet happens to write with."""
    old_key_1 = Fernet.generate_key().decode()
    old_key_2 = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    # Encrypt each legacy ciphertext directly under its own key: MultiFernet always writes with its
    # first key, so routing both through TokenCipher would leave old_key_2's decrypt path untested.
    legacy_ciphertext_1 = Fernet(old_key_1).encrypt(b"token").decode()
    legacy_ciphertext_2 = Fernet(old_key_2).encrypt(b"token").decode()

    configure_token_encryption(new_key, old_key_1, old_key_2)

    # Both legacy keys still decrypt after rotation.
    assert TokenCipher.decrypt(legacy_ciphertext_1) == "token"
    assert TokenCipher.decrypt(legacy_ciphertext_2) == "token"

    # A fresh write is encrypted under the new primary key only — neither legacy key can read it.
    rotated_ciphertext = TokenCipher.encrypt("token")
    assert Fernet(new_key).decrypt(rotated_ciphertext.encode()).decode() == "token"
    with pytest.raises(InvalidToken):
        Fernet(old_key_1).decrypt(rotated_ciphertext.encode())
    with pytest.raises(InvalidToken):
        Fernet(old_key_2).decrypt(rotated_ciphertext.encode())


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
