import datetime as dt
from typing import ClassVar

from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    FetchedValue,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from mitup_bot.exceptions import TokenEncryptionNotConfigured
from mitup_bot.supporter import SupporterLevel

from .base_model import BaseModel


class TokenCipher:
    """Holds the process-wide MultiFernet used to encrypt Patreon token columns.

    The key(s) are injected at startup via `configure_token_encryption` rather than read from
    config at import time, so the models package never imports the config layer. Encryption raises
    until at least one key has been configured.
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
    setup (bot runtime / CLI) with `*PatreonConfig.encryption_keys()`.
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


class SupporterSubscription(BaseModel, SQLModel, table=True):
    __tablename__: str = "supporter_subscriptions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    )
    # Captured from Patreon's /identity at link time; the join key against the daily bulk
    # member list. Unique so one Patreon account cannot grant support to several TG accounts.
    patreon_user_id: str = Field(sa_column=Column(String, unique=True, nullable=False))
    support_expiration: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    expiration_notified: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=false()))
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )


class PatreonPendingLink(BaseModel, SQLModel, table=True):
    """A Patreon identity proven by OAuth consent, parked until a Telegram account confirms it.

    The OAuth callback cannot know whose Telegram account the consent belongs to, so it writes what
    it *does* know here and hands out a pairing code. The row then moves through two transitions,
    each a conditional update keyed on ``code_hash`` alone, and each re-checking ``expiration``
    against the database clock so a prompt opened just before the deadline cannot be confirmed after
    it:

    * **claim** — the code arrives over Telegram and ``claimed_tg_user_id`` records who presented it.
    * **consume** — that same account confirms, ``consumed_time`` is stamped, and the row is spent.

    Nothing here grants anything on its own, and a row may sit claimed but unconfirmed until it
    expires. One person may legitimately hold several live rows (starting the flow twice), so no
    lookup is ever keyed by the claiming user and no column of theirs is unique.
    """

    __tablename__: str = "patreon_pending_links"

    id: int | None = Field(default=None, primary_key=True)
    # SHA-256 of the pairing code. The code itself is never stored, so a database read cannot be
    # turned back into a redeemable link. Unique because a collision would be an ambiguous claim.
    code_hash: str = Field(sa_column=Column(String, unique=True, nullable=False))
    # The Patreon identity the consent proved, copied onto the subscription row at redemption.
    patreon_user_id: str = Field(sa_column=Column(String, nullable=False))
    # Patreon's display name for that identity, shown in the confirmation prompt so the person
    # confirming reads a name rather than an opaque id. Nullable: Patreon omits it when the account
    # has not set one, and the prompt falls back to naming no one rather than failing.
    patreon_full_name: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    # The tier the identity was entitled to when the consent completed, and the only authority for
    # what the redemption grants. It is stored rather than recomputed because the access token is
    # discarded at the end of the callback; the resulting staleness is bounded by `expiration` and
    # corrected by the daily reconciliation.
    supporter_level: SupporterLevel = Field(
        default=SupporterLevel.NONE,
        sa_column=Column(
            Enum(
                SupporterLevel,
                native_enum=False,
                length=16,
                values_callable=lambda enum: [member.value for member in enum],
            ),
            nullable=False,
            server_default=SupporterLevel.NONE.value,
        ),
    )
    expiration: dt.datetime = Field(sa_column=Column(DateTime, nullable=False))
    # The Telegram account that presented the code. Deliberately not unique: one person can hold
    # several live rows, and a uniqueness error would turn an honest double-start into a failure.
    # The confirm transition matches on it so a code claimed by one account cannot be confirmed by
    # another. BigInteger to match `users.tg_user_id`, which outgrew a 32-bit column.
    claimed_tg_user_id: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    claimed_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
    # Set when the confirmation goes through; a non-null value makes the row permanently unusable.
    consumed_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, nullable=True))
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
    # so the config pair is adopted (self-service reset via CI variable).
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
