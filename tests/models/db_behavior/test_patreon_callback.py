"""Real-Postgres proof of the OAuth callback's persistence (``link_patreon_account``).

The callback drives ``db.begin_write``: it opens its own transaction and commits, so the seeds here
must be committed too (the session-fixture's uncommitted rows are invisible to that transaction).
Committed cross-session data uses the 997 range; this file claims the 997_6xx sub-range.
"""

import contextlib
import datetime as dt
from collections.abc import AsyncIterator, Iterator
from typing import cast

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.ext import ExtBot

from mitup_bot import db
from mitup_bot.api_wrapper import BotAdapter, TelegramApi
from mitup_bot.models import PremiumSubscription, Settings, User, configure_token_encryption
from mitup_bot.models.premium import TokenCipher
from mitup_bot.monitoring.backend import NullBackend
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.patreon import TokenPair
from mitup_bot.web.patreon import LinkOutcome, link_patreon_account

pytestmark = pytest.mark.db_test

PATRON_USER_ID = "patreon-6001"
NON_PATRON_USER_ID = "patreon-6002"


@pytest.fixture(autouse=True)
def configured_db(db_session: AsyncSession) -> AsyncSession:
    """Depend on the session-scoped ``db_session`` so ``configure_db`` has run before the callback
    opens its own ``db.begin`` transactions."""
    return db_session


@pytest.fixture(autouse=True, scope="module")
def configured_token_encryption() -> Iterator[None]:
    saved = TokenCipher.cipher
    configure_token_encryption(Fernet.generate_key().decode())
    try:
        yield
    finally:
        TokenCipher.cipher = saved


class RecordingBot:
    """Captures the confirmation DM the write lifecycle drains after commit."""

    def __init__(self):
        self.sent: list[dict[str, object]] = []

    async def send_message(self, **kwargs: object):
        self.sent.append(kwargs)


def make_api(bot: RecordingBot) -> TelegramApi:
    api = TelegramApi()
    api.adapter = BotAdapter(cast(ExtBot, bot), MetricsClient(NullBackend()))
    return api


def fresh_pair() -> TokenPair:
    return TokenPair("fresh-access", "fresh-refresh", dt.datetime.now(dt.UTC) + dt.timedelta(days=30))


@contextlib.asynccontextmanager
async def committed_user(tg_user_id: int) -> AsyncIterator[int]:
    async with db.begin() as session:
        user = User(first_name="Callback User", tg_user_id=tg_user_id, settings=Settings())
        session.add(user)
        await session.flush()
        user_id = user.db_id
    try:
        yield user_id
    finally:
        # premium_subscriptions has ON DELETE CASCADE, so removing the user clears its row too.
        async with db.begin() as session:
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id = :t").bindparams(t=tg_user_id)
            )


async def read_user_and_subscription(user_id: int) -> tuple[User, PremiumSubscription | None]:
    async with db.begin() as session:
        user = (await session.exec(select(User).where(User.id == user_id))).one()
        subscription = (
            await session.exec(select(PremiumSubscription).where(PremiumSubscription.user_id == user_id))
        ).first()
        return user, subscription


async def test_new_link_for_patron_grants_premium():
    async with committed_user(997_600) as user_id:
        bot = RecordingBot()
        outcome = await link_patreon_account(
            make_api(bot), 997_600, fresh_pair(), patreon_user_id=PATRON_USER_ID, is_active_member=True
        )

        assert outcome is LinkOutcome.LINKED_PREMIUM
        user, subscription = await read_user_and_subscription(user_id)
        assert user.is_premium is True
        assert subscription is not None
        assert subscription.patreon_user_id == "patreon-6001"
        assert subscription.access_token == "fresh-access"
        assert subscription.premium_expiration is not None
        assert len(bot.sent) == 1


async def test_new_link_for_non_patron_stores_without_premium():
    async with committed_user(997_610) as user_id:
        bot = RecordingBot()
        outcome = await link_patreon_account(
            make_api(bot), 997_610, fresh_pair(), patreon_user_id=NON_PATRON_USER_ID, is_active_member=False
        )

        assert outcome is LinkOutcome.LINKED_NO_PATRON
        user, subscription = await read_user_and_subscription(user_id)
        assert user.is_premium is False
        assert subscription is not None
        assert subscription.premium_expiration is None
        assert len(bot.sent) == 1


async def test_relink_during_grace_updates_in_place_and_clears_revoke():
    async with committed_user(997_620) as user_id:
        async with db.begin() as session:
            session.add(
                PremiumSubscription(
                    user_id=user_id,
                    patreon_user_id="patreon-6001",
                    access_token="stale-access",
                    refresh_token="stale-refresh",
                    token_expiration=dt.datetime.now(dt.UTC),
                    revoked_time=dt.datetime.now(dt.UTC),
                )
            )
            await session.flush()
            original_id = (
                (await session.exec(select(PremiumSubscription).where(PremiumSubscription.user_id == user_id)))
                .one()
                .db_id
            )

        outcome = await link_patreon_account(
            make_api(RecordingBot()), 997_620, fresh_pair(), patreon_user_id=PATRON_USER_ID, is_active_member=True
        )

        assert outcome is LinkOutcome.LINKED_PREMIUM
        user, subscription = await read_user_and_subscription(user_id)
        assert subscription is not None
        assert subscription.db_id == original_id  # updated in place, not recreated
        assert subscription.revoked_time is None
        assert subscription.access_token == "fresh-access"
        assert user.is_premium is True


async def test_patreon_account_already_linked_to_another_user_is_rejected():
    async with committed_user(997_630) as first_user_id, committed_user(997_631) as second_user_id:
        async with db.begin() as session:
            session.add(
                PremiumSubscription(
                    user_id=first_user_id,
                    patreon_user_id="patreon-shared-663",
                    access_token="a",
                    refresh_token="r",
                    token_expiration=dt.datetime.now(dt.UTC),
                )
            )
            await session.flush()

        outcome = await link_patreon_account(
            make_api(RecordingBot()), 997_631, fresh_pair(), patreon_user_id="patreon-shared-663", is_active_member=True
        )

        assert outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE
        second_user, second_subscription = await read_user_and_subscription(second_user_id)
        assert second_subscription is None
        assert second_user.is_premium is False


async def test_unknown_user_returns_unknown_outcome():
    outcome = await link_patreon_account(
        make_api(RecordingBot()), 997_699, fresh_pair(), patreon_user_id=PATRON_USER_ID, is_active_member=True
    )
    assert outcome is LinkOutcome.UNKNOWN_USER
