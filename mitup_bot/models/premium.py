import datetime as dt
from typing import ClassVar

from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import Boolean, Column, DateTime, FetchedValue, ForeignKey, Integer, String, Text, false
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from mitup_bot.exceptions import TokenEncryptionNotConfigured

from .base_model import BaseModel


class TokenCipher:
    """Holds the process-wide MultiFernet used to encrypt Patreon token columns.

    The key(s) are injected at startup via `configure_token_encryption` rather than read from
    config at import time, so the models package never imports the config layer (the Patreon
    config lands in a separate change). Encryption raises until at least one key has been configured.
    """

    cipher: ClassVar[MultiFernet | None] = None

    @classmethod
    def encrypt(cls, value: str) -> str:
        if cls.cipher is None:
            raise TokenEncryptionNotConfigured
        return cls.cipher.encrypt(value.encode()).decode()

    @classmethod
    def decrypt(cls, value: str) -> str:
        if cls.cipher is None:
            raise TokenEncryptionNotConfigured
        return cls.cipher.decrypt(value.encode()).decode()


def configure_token_encryption(*keys: str):
    """Inject the Fernet key(s) used to encrypt Patreon token columns at rest.

    The first key is primary and encrypts every new write; all keys decrypt, so passing
    `(new, old)` during a rotation keeps legacy ciphertext readable while the daily token
    refresh re-encrypts everything under the new key within a day. Called once during process
    setup (bot runtime / CLI); a later change wires this to `PatreonConfig.encryption_key`.
    """
    if not keys:
        raise ValueError("configure_token_encryption requires at least one Fernet key")
    TokenCipher.cipher = MultiFernet([Fernet(key) for key in keys])


class EncryptedToken(TypeDecorator):
    """Column type that transparently Fernet-encrypts a token string on write and decrypts on read."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return TokenCipher.encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return TokenCipher.decrypt(value)


class PremiumSubscription(BaseModel, SQLModel, table=True):
    __tablename__: str = "premium_subscriptions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    )
    # Captured from Patreon's /identity at link time; the join key against the daily bulk
    # member list. Unique so one Patreon account cannot grant premium to several TG accounts.
    patreon_user_id: str = Field(sa_column=Column(String, unique=True, nullable=False))
    # Set when a refresh fails with invalid_grant (user disconnected the app on Patreon's side).
    # The row survives through the grace period so a re-link is a token update, not a from-scratch
    # flow; revoked rows are excluded from token refresh and TTL metrics.
    revoked_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    premium_expiration: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    expiration_notified: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=false()))
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )


class PatreonCreatorToken(BaseModel, SQLModel, table=True):
    __tablename__: str = "patreon_creator_tokens"

    id: int | None = Field(default=None, primary_key=True)
    access_token: str = Field(sa_column=Column(EncryptedToken, nullable=False))
    refresh_token: str = Field(sa_column=Column(EncryptedToken, nullable=False))
    token_expiration: dt.datetime = Field(sa_column=Column(DateTime, nullable=False))
    # SHA-256 of the seed access token that initialized (or last reset) this row. The daily job
    # compares it against the configured seed: a mismatch means the operator provided a new seed,
    # so the config pair is adopted (self-service reset via CI variable, see #158).
    seed_fingerprint: str = Field(sa_column=Column(String, nullable=False))
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )


class PatreonWebhook(BaseModel, SQLModel, table=True):
    __tablename__: str = "patreon_webhooks"

    id: int | None = Field(default=None, primary_key=True)
    # Patreon-side id of the single registered webhook; needed to PATCH it when its uri drifts.
    patreon_webhook_id: str = Field(sa_column=Column(String, unique=True, nullable=False))
    # The receiving URI including its query params, so startup can detect drift against the configured one.
    uri: str = Field(sa_column=Column(String, nullable=False))
    # HMAC signing secret, Fernet-encrypted at rest exactly like the other token columns.
    secret: str = Field(sa_column=Column(EncryptedToken, nullable=False))
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )
