"""Real-Postgres proof of the `patreon_webhooks` table added by the webhook-secret-storage migration.

The migration applies as part of the session-scoped `migrated_db` upgrade-to-head; here we prove the
row round-trips with the `secret` transparently Fernet-encrypted on write / decrypted on read, that
the timestamp triggers populate `created_time`/`updated_time`, and that `patreon_webhook_id` is
unique. Encryption is configured with a throwaway key for the module, mirroring the peer creator-token
and premium-subscription tests.
"""

from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import PatreonWebhook, configure_token_encryption
from mitup_bot.models.subscriptions import TokenCipher
from tests.helpers import create_patreon_webhook

pytestmark = pytest.mark.db_test


@pytest.fixture(autouse=True, scope="module")
def configured_token_encryption() -> Iterator[None]:
    """Encrypt/decrypt the secret column with a throwaway key for the duration of this module."""
    saved = TokenCipher.cipher
    configure_token_encryption(Fernet.generate_key().decode())
    try:
        yield
    finally:
        TokenCipher.cipher = saved


async def test_table_exists(db_session: AsyncSession):
    result = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT COUNT(*) FROM information_schema.tables"
                " WHERE table_schema='public' AND table_name='patreon_webhooks'"
            )
        )
    ).scalar_one()
    assert result == 1


async def test_secret_encrypts_at_rest(db_session: AsyncSession):
    webhook = create_patreon_webhook(patreon_webhook_id="wh-encrypt", secret="plain-signing-secret")
    db_session.add(webhook)
    await db_session.flush()
    webhook_id = webhook.db_id

    # Expire the identity-mapped instance so the reload runs the decrypting result processor.
    db_session.expire(webhook)
    loaded = (await db_session.exec(select(PatreonWebhook).where(PatreonWebhook.id == webhook_id))).one()
    assert loaded.secret == "plain-signing-secret"
    assert loaded.patreon_webhook_id == "wh-encrypt"
    assert loaded.uri == "https://bot.example/patreon/webhook"

    stored_secret = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("SELECT secret FROM patreon_webhooks WHERE id = :id").bindparams(id=webhook_id)
        )
    ).scalar_one()
    assert stored_secret != "plain-signing-secret"
    assert TokenCipher.decrypt(stored_secret) == "plain-signing-secret"


async def test_timestamps_set_on_insert(db_session: AsyncSession):
    webhook = create_patreon_webhook(patreon_webhook_id="wh-timestamps")
    db_session.add(webhook)
    await db_session.flush()
    await db_session.refresh(webhook)

    assert webhook.created_time is not None
    assert webhook.updated_time is not None


async def test_patreon_webhook_id_is_unique(db_session: AsyncSession):
    db_session.add(create_patreon_webhook(patreon_webhook_id="wh-shared"))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(create_patreon_webhook(patreon_webhook_id="wh-shared"))
            await db_session.flush()
